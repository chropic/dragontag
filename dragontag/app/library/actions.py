"""Additional individual library actions.

Each function operates on a single LibraryFolder and is invoked from a daemon
thread by the route layer. Failures on individual tracks are logged but never
abort the run.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from sqlmodel import select

from ..db import session
from ..models import LibraryFolder, Track

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extract embedded cover art
# ---------------------------------------------------------------------------


def extract_embedded_covers(folder_id: int) -> dict:
    """For each track, if no cover.jpg/png exists in its parent folder, write
    one from the embedded picture data."""
    written = 0
    skipped = 0
    errors = 0
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    seen_dirs: set[Path] = set()
    for t in tracks:
        p = Path(t.path)
        if not p.exists():
            continue
        parent = p.parent
        if parent in seen_dirs:
            continue
        seen_dirs.add(parent)
        target = parent / "cover.jpg"
        if target.exists() or (parent / "cover.png").exists():
            skipped += 1
            continue
        try:
            data, ext = _read_embedded_picture(p)
            if not data:
                continue
            out = parent / f"cover.{ext}"
            out.write_bytes(data)
            written += 1
        except Exception as e:
            errors += 1
            log.debug("extract cover %s: %s", p, e)
    summary = {"written": written, "skipped": skipped, "errors": errors}
    log.info("extract_embedded_covers(%d): %s", folder_id, summary)
    return summary


def _read_embedded_picture(path: Path) -> tuple[bytes | None, str]:
    """Return (bytes, ext) of the embedded front-cover, or (None, '')."""
    try:
        from mutagen import File as MFile
        from mutagen.flac import FLAC, Picture
        from mutagen.id3 import ID3, APIC
        from mutagen.mp4 import MP4
    except Exception:
        return None, ""

    f = MFile(str(path))
    if f is None:
        return None, ""

    # FLAC
    if isinstance(f, FLAC):
        for pic in f.pictures:
            mime = (pic.mime or "image/jpeg").lower()
            ext = "png" if "png" in mime else "jpg"
            return pic.data, ext
        # block-encoded
        if f.tags and "metadata_block_picture" in f.tags:
            import base64
            blob = base64.b64decode(f.tags["metadata_block_picture"][0])
            pic = Picture(blob)
            ext = "png" if "png" in (pic.mime or "").lower() else "jpg"
            return pic.data, ext
        return None, ""

    # MP4 / M4A
    if isinstance(f, MP4):
        covr = f.tags.get("covr") if f.tags else None
        if covr:
            data = bytes(covr[0])
            ext = "png" if data[:4] == b"\x89PNG" else "jpg"
            return data, ext
        return None, ""

    # ID3 (mp3/wav)
    try:
        tags = ID3(str(path))
    except Exception:
        return None, ""
    for k, v in tags.items():
        if isinstance(v, APIC):
            ext = "png" if "png" in (v.mime or "").lower() else "jpg"
            return v.data, ext
    return None, ""


# ---------------------------------------------------------------------------
# ReplayGain
# ---------------------------------------------------------------------------


def recompute_replaygain(folder_id: int) -> dict:
    """Invoke rsgain / loudgain across albums if available on PATH."""
    tool = shutil.which("rsgain") or shutil.which("loudgain")
    if not tool:
        return {"ok": False, "reason": "Neither rsgain nor loudgain is on PATH"}
    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"ok": False, "reason": "Folder not found"}
        lib_root = folder.path
    try:
        if "rsgain" in tool:
            cmd = [tool, "easy", lib_root]
        else:
            cmd = [tool, "-a", "-k", lib_root]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60)
        return {"ok": r.returncode == 0, "rc": r.returncode, "stderr": r.stderr[-500:]}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------


def verify_integrity(folder_id: int) -> dict:
    """Open every file via mutagen; collect any that fail to load."""
    try:
        from mutagen import File as MFile
    except Exception:
        return {"ok": False, "reason": "mutagen not available"}
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    bad: list[str] = []
    checked = 0
    for t in tracks:
        p = Path(t.path)
        if not p.exists():
            bad.append(f"missing: {t.path}")
            continue
        checked += 1
        try:
            f = MFile(str(p))
            if f is None:
                bad.append(f"unreadable: {t.path}")
                continue
            # Force a header read; mutagen lazy-loads info.
            _ = getattr(f, "info", None)
        except Exception as e:
            bad.append(f"{t.path}: {e}")
    summary = {"checked": checked, "bad": bad[:50], "bad_count": len(bad)}
    log.info("verify_integrity(%d): %s", folder_id, {"checked": checked, "bad_count": len(bad)})
    return summary


# ---------------------------------------------------------------------------
# Disc-folder correction
# ---------------------------------------------------------------------------


_DISC_RE = re.compile(r"^(?:disc|cd|disk)\s*0*(\d+)$", re.IGNORECASE)


def fix_disc_folders(folder_id: int) -> dict:
    """Normalize album folders so that multi-disc releases live under uniformly
    named ``Disc N`` subfolders. Single-disc folders that contain a stray
    ``Disc 1`` subfolder have it flattened.

    No tag rewriting: just moves files into / out of subfolders. Updates
    Track.path in the DB to reflect the new locations.
    """
    from ..config import settings as _settings

    template = _settings().multidisc_folder_template
    renamed = 0
    flattened = 0
    errors = 0

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
        # group by album folder (parent of parent for multi-disc, else parent)
        album_dirs: set[Path] = set()
        for t in tracks:
            p = Path(t.path).parent
            # If parent name looks like Disc N, the album dir is p.parent.
            if _DISC_RE.match(p.name):
                album_dirs.add(p.parent)
            else:
                album_dirs.add(p)

        for album in album_dirs:
            if not album.exists():
                continue
            disc_children = [c for c in album.iterdir() if c.is_dir() and _DISC_RE.match(c.name)]
            if not disc_children:
                continue
            if len(disc_children) == 1:
                # Single Disc 1/ folder under an otherwise single-disc album: flatten.
                disc_dir = disc_children[0]
                for f in list(disc_dir.iterdir()):
                    try:
                        target = album / f.name
                        if target.exists():
                            continue
                        shutil.move(str(f), str(target))
                        _update_track_path(s, str(f), str(target))
                        flattened += 1
                    except Exception:
                        errors += 1
                try:
                    if not any(disc_dir.iterdir()):
                        os.rmdir(disc_dir)
                except OSError:
                    pass
                continue
            # Multi-disc: normalize names
            for d in disc_children:
                m = _DISC_RE.match(d.name)
                if not m:
                    continue
                n = int(m.group(1))
                want = template.format(disc=n)
                if d.name == want:
                    continue
                try:
                    new_path = album / want
                    if new_path.exists():
                        continue
                    d.rename(new_path)
                    # Update every track whose path lived under the old disc dir.
                    for t in tracks:
                        if t.path.startswith(str(d) + os.sep):
                            new_track_path = t.path.replace(str(d), str(new_path), 1)
                            db_t = s.get(Track, t.id)
                            if db_t:
                                db_t.path = new_track_path
                                s.add(db_t)
                    renamed += 1
                except Exception:
                    errors += 1
        s.commit()

    summary = {"renamed": renamed, "flattened": flattened, "errors": errors}
    log.info("fix_disc_folders(%d): %s", folder_id, summary)
    return summary


def _update_track_path(s, old: str, new: str) -> None:
    t = s.exec(select(Track).where(Track.path == old)).first()
    if t:
        t.path = new
        s.add(t)


# ---------------------------------------------------------------------------
# Missing-track finder
# ---------------------------------------------------------------------------


def find_missing_tracks(folder_id: int) -> dict:
    """Group tracks by MusicBrainz album ID and compare local count to the MB
    release's track count.

    Returns a list of ``{"album": str, "local": int, "expected": int}`` entries
    for releases where local < expected. Releases without an MB ID are skipped.
    """
    from ..identify import musicbrainz as mbq

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()

    grouped: dict[str, list[Track]] = {}
    for t in tracks:
        if not t.mb_album_id:
            continue
        grouped.setdefault(t.mb_album_id, []).append(t)

    missing: list[dict] = []
    for mb_album_id, group in grouped.items():
        try:
            rel = mbq.fetch_release(mb_album_id)
            expected = 0
            for medium in rel.get("medium-list", []) or []:
                expected += int(medium.get("track-count") or 0)
            if expected and len(group) < expected:
                missing.append({
                    "album": group[0].album or mb_album_id,
                    "artist": group[0].album_artist or group[0].artist or "",
                    "local": len(group),
                    "expected": expected,
                })
        except Exception as e:
            log.debug("find_missing_tracks %s: %s", mb_album_id, e)
    summary = {"missing": missing[:200], "count": len(missing)}
    log.info("find_missing_tracks(%d): %s", folder_id, {"count": len(missing)})
    return summary
