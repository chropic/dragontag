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
from collections import Counter
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..db import session
from ..models import LibraryFolder, Track
from ..timeutil import now_utc
from . import filelock
from .mover import _image_width
from .mover import move as _safe_move
from .mover import move_lyric_sidecar as _move_lyric_sidecar
from .paths import (
    album_fold_key,
    artist_fold_key,
    fold_text,
    primary_artist,
    sanitize_segment,
    strip_edition_suffixes,
    unique_path,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetch lyrics / covers (shared by the route layer and the scheduler)
# ---------------------------------------------------------------------------


def fetch_lyrics_for_folder(folder_id: int, ctx=None) -> dict:
    """Fetch and embed lyrics for all tracks in a folder without re-tagging.

    ``ctx`` is an optional ``tasks.TaskCtx`` for progress reporting.
    """
    from ..tagging import lyrics_fetcher
    from ..tagging.advisory import is_explicit
    from ..tagging.partial import write_lyrics

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    items = [
        (t.id, t.title, t.artist, t.album, Path(t.path))
        for t in tracks if not t.protected and Path(t.path).exists()
    ]
    if ctx:
        ctx.progress(0, len(items))

    fetched_count = 0
    for i, (track_id, title, artist, album, p) in enumerate(items, start=1):
        if ctx:
            ctx.check_cancelled()
        try:
            fetched = lyrics_fetcher.fetch(artist=artist, title=title, album=album)
            if fetched:
                advisory = 1 if is_explicit(fetched) else 0
                # path_lock: in-place mutator — serialize against the ingest
                # worker / organizer / revert on the same physical file.
                with filelock.path_lock(p):
                    write_lyrics(p, fetched, advisory)
                fetched_count += 1
                # Keep the DB in sync so the dashboard counters update without
                # requiring a full re-scan. The lyrics are already on disk, so a
                # DB failure here is a recoverable cache miss (a re-scan rebuilds
                # has_lyrics/advisory) — log it explicitly instead of letting it
                # look like the embed itself failed.
                try:
                    with session() as s2:
                        t = s2.get(Track, track_id)
                        if t:
                            t.has_lyrics = True
                            t.advisory = advisory
                            s2.add(t)
                            s2.commit()
                except Exception:
                    log.exception(
                        "fetch-lyrics: DB sync failed for %s (lyrics already written)", p
                    )
        except Exception:
            log.exception("fetch-lyrics: failed for %s", p)
        if ctx:
            ctx.progress(i, len(items))
    if ctx:
        ctx.log(f"Lyrics embedded for {fetched_count}/{len(items)} track(s)")
    return {"processed": len(items), "fetched": fetched_count}


def fetch_covers_for_folder(folder_id: int, ctx=None) -> dict:
    """Fetch and embed cover art for tracks that have MusicBrainz album IDs."""
    from ..config import settings
    from ..tagging.coverart import fetch_for_release
    from ..tagging.partial import write_cover
    from .mover import write_cover_jpg

    with session() as s:
        tracks = s.exec(select(Track).where(
            Track.library_folder_id == folder_id,
            Track.mb_album_id.is_not(None),
        )).all()
    items = [
        (Path(t.path), t.mb_album_id)
        for t in tracks if not t.protected and Path(t.path).exists()
    ]
    if ctx:
        ctx.progress(0, len(items))

    fetched_count = 0
    for i, (p, mb_album_id) in enumerate(items, start=1):
        if ctx:
            ctx.check_cancelled()
        try:
            cover = fetch_for_release(mb_album_id)
            if cover:
                with filelock.path_lock(p):
                    write_cover(p, cover.data, cover.mime)
                write_cover_jpg(
                    p.parent, cover.data,
                    min_overwrite_pixels=settings().cover_min_overwrite_pixels,
                    new_width=cover.width,
                )
                fetched_count += 1
        except Exception:
            log.exception("fetch-covers: failed for %s", p)
        if ctx:
            ctx.progress(i, len(items))
    if ctx:
        ctx.log(f"Covers fetched for {fetched_count}/{len(items)} track(s)")
    return {"processed": len(items), "fetched": fetched_count}


# ---------------------------------------------------------------------------
# Extract embedded cover art
# ---------------------------------------------------------------------------


def extract_embedded_covers(folder_id: int, ctx=None) -> dict:
    """For each track, if no cover.jpg/png exists in its parent folder, write
    one from the embedded picture data."""
    written = 0
    skipped = 0
    errors = 0
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    seen_dirs: set[Path] = set()
    if ctx:
        ctx.progress(0, len(tracks))
    for i, t in enumerate(tracks, start=1):
        p = Path(t.path)
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(tracks), item=p.name)
        if t.protected or not p.exists():
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
    if ctx:
        ctx.log(f"Covers written for {written} folder(s), {skipped} already had one, {errors} error(s)")
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


def _find_replaygain_tool() -> str | None:
    """Locate the rsgain/loudgain binary.

    Checks, in order: the configured ``replaygain_tool_path`` setting, the
    system PATH, then common install dirs. Returns the absolute path or None.
    """
    from ..config import settings

    configured = (settings().replaygain_tool_path or "").strip()
    if configured and os.access(configured, os.X_OK):
        return configured

    for name in ("rsgain", "loudgain"):
        found = shutil.which(name)
        if found:
            return found
        for d in ("/usr/bin", "/usr/local/bin"):
            cand = os.path.join(d, name)
            if os.access(cand, os.X_OK):
                return cand
    return None


def recompute_replaygain(folder_id: int, ctx=None) -> dict:
    """Invoke rsgain / loudgain per album folder if available.

    Running per album directory (rather than one process over the whole
    library) keeps album-gain semantics identical while giving real progress.
    """
    tool = _find_replaygain_tool()
    if not tool:
        if ctx:
            ctx.log(
                "Neither rsgain nor loudgain found — install one, or set "
                "'replaygain_tool_path' in Settings — skipping"
            )
        return {"ok": False, "reason": "rsgain/loudgain not found"}
    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"ok": False, "reason": "Folder not found"}
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()

    album_dirs: dict[Path, list[Path]] = {}
    for t in tracks:
        p = Path(t.path)
        if not t.protected and p.exists():
            album_dirs.setdefault(p.parent, []).append(p)
    if not album_dirs:
        return {"ok": True, "albums": 0, "failed": 0}

    failed = 0
    dirs = sorted(album_dirs)
    if ctx:
        ctx.progress(0, len(dirs))
    for i, d in enumerate(dirs, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(dirs), item=d.name)
        try:
            if "rsgain" in tool:
                cmd = [tool, "easy", str(d)]
            else:
                cmd = [tool, "-a", "-k", *(str(f) for f in album_dirs[d])]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 10)
            if r.returncode != 0:
                failed += 1
                if ctx:
                    ctx.log(f"{d.name}: rc={r.returncode} {r.stderr[-200:].strip()}")
        except Exception as e:
            failed += 1
            if ctx:
                ctx.log(f"{d.name}: {e}")
    if ctx:
        ctx.log(f"ReplayGain done for {len(dirs) - failed}/{len(dirs)} album folder(s)")
    return {"ok": failed == 0, "albums": len(dirs), "failed": failed}


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------


def verify_integrity(folder_id: int, ctx=None) -> dict:
    """Open every file via mutagen; collect any that fail to load."""
    try:
        from mutagen import File as MFile
    except Exception:
        return {"ok": False, "reason": "mutagen not available"}
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    bad: list[str] = []
    checked = 0
    if ctx:
        ctx.progress(0, len(tracks))
    for i, t in enumerate(tracks, start=1):
        p = Path(t.path)
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(tracks), item=p.name)
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
    if ctx:
        for line in bad[:50]:
            ctx.log(line)
        ctx.log(f"Checked {checked} file(s), {len(bad)} problem(s)")
    return summary


def _update_track_path(s, old: str, new: str) -> None:
    t = s.exec(select(Track).where(Track.path == old)).first()
    if t:
        t.path = new
        s.add(t)


# ---------------------------------------------------------------------------
# Missing-track finder
# ---------------------------------------------------------------------------


def find_missing_tracks(folder_id: int, ctx=None) -> dict:
    """Group tracks by MusicBrainz album ID and compare local count to the MB
    release's track count.

    Persists one ``IncompleteAlbum`` row per under-complete release (replacing
    the folder's previous results) so the Library page's "Incomplete" tab can
    render them later. Releases without an MB ID are skipped.
    """
    from ..identify import musicbrainz as mbq
    from ..models import IncompleteAlbum

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()

    grouped: dict[str, list[Track]] = {}
    for t in tracks:
        if not t.mb_album_id:
            continue
        grouped.setdefault(t.mb_album_id, []).append(t)

    missing: list[dict] = []
    if ctx:
        ctx.progress(0, len(grouped))
    for i, (mb_album_id, group) in enumerate(grouped.items(), start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(grouped), item=group[0].album or mb_album_id)
        try:
            rel = mbq.fetch_release(mb_album_id)
            expected = 0
            mb_titles: list[tuple[int, int | None, str]] = []
            for medium in rel.get("medium-list", []) or []:
                expected += int(medium.get("track-count") or 0)
                disc = int(medium.get("position") or 1)
                for tr in medium.get("track-list", []) or []:
                    title = (tr.get("recording") or {}).get("title") or tr.get("title") or ""
                    num = tr.get("position")
                    mb_titles.append((disc, int(num) if num else None, title))
            if expected and len(group) < expected:
                # Identify which MB (disc, track) slots are absent locally so
                # the Incomplete tab can show *what* is missing, not just counts.
                local_nums = {(t.disc_num or 1, t.track_num) for t in group if t.track_num}
                missing_titles = [
                    f"{disc}-{num:02d}. {title}" if num else title
                    for disc, num, title in mb_titles
                    if num is None or (disc, num) not in local_nums
                ][:50]
                missing.append({
                    "mb_album_id": mb_album_id,
                    "album": group[0].album or mb_album_id,
                    "artist": group[0].album_artist or group[0].artist or "",
                    "local": len(group),
                    "expected": expected,
                    "missing_titles": missing_titles,
                })
                if ctx:
                    ctx.log(f"{group[0].album or mb_album_id}: {len(group)}/{expected} tracks")
        except Exception as e:
            log.debug("find_missing_tracks %s: %s", mb_album_id, e)

    # Replace this folder's previous results wholesale — stale rows for albums
    # that are now complete must disappear.
    with session() as s:
        for row in s.exec(select(IncompleteAlbum).where(
                IncompleteAlbum.library_folder_id == folder_id)).all():
            s.delete(row)
        for m in missing:
            s.add(IncompleteAlbum(
                library_folder_id=folder_id,
                mb_album_id=m["mb_album_id"],
                album=m["album"],
                artist=m["artist"],
                local_count=m["local"],
                expected_count=m["expected"],
                missing_titles_json=m["missing_titles"],
            ))
        s.commit()

    summary = {"missing": [
        {k: m[k] for k in ("album", "artist", "local", "expected")} for m in missing[:200]
    ], "count": len(missing)}
    log.info("find_missing_tracks(%d): %s", folder_id, {"count": len(missing)})
    if ctx:
        ctx.log(f"Checked {len(grouped)} album(s): {len(missing)} incomplete — see the Library page's Incomplete tab")
    return summary


# ---------------------------------------------------------------------------
# Advisory re-evaluation (moved out of the route layer so chains can reuse it)
# ---------------------------------------------------------------------------


def tag_advisories_for_folder(folder_id: int, ctx=None) -> dict:
    """Re-evaluate the explicit-advisory rating from each track's embedded lyrics."""
    from ..tagging.advisory import is_explicit
    from ..tagging.partial import read_lyrics, write_advisory

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    items = [
        (t.id, Path(t.path)) for t in tracks if not t.protected and Path(t.path).exists()
    ]
    if ctx:
        ctx.progress(0, len(items))

    tagged = 0
    for i, (track_id, p) in enumerate(items, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(items), item=p.name)
        try:
            # path_lock around the read-then-write pair: the advisory is
            # derived from the lyrics read moments earlier, so nothing may
            # rewrite the file in between.
            with filelock.path_lock(p):
                lyrics = read_lyrics(p)
                if not lyrics:
                    continue
                advisory = 1 if is_explicit(lyrics) else 0
                write_advisory(p, advisory)
            tagged += 1
            # Reflect the re-evaluated rating (and the fact that lyrics are
            # present) in the DB so the dashboard stays accurate. The advisory
            # is already on disk; a DB failure is a recoverable cache miss, so
            # log it explicitly rather than masking it as a write failure.
            try:
                with session() as s2:
                    t = s2.get(Track, track_id)
                    if t:
                        t.advisory = advisory
                        t.has_lyrics = True
                        s2.add(t)
                        s2.commit()
            except Exception:
                log.exception(
                    "tag-advisories: DB sync failed for %s (advisory already written)", p
                )
        except Exception:
            log.exception("tag-advisories: failed for %s", p)
    if ctx:
        ctx.log(f"Advisory re-evaluated for {tagged}/{len(items)} track(s) with lyrics")
    return {"processed": len(items), "tagged": tagged}


# ---------------------------------------------------------------------------
# Genre backfill (only fills tracks that currently have no genre)
# ---------------------------------------------------------------------------


def fix_genres_for_folder(folder_id: int, ctx=None) -> dict:
    """Backfill missing genres from MusicBrainz for tracks that have none.

    Only tracks whose embedded genre is empty are touched — an existing genre is
    never overwritten. Genres come from the recording's community tags, falling
    back to the release-group's (which is far more often tagged). Tracks with no
    MusicBrainz id, and those whose MB entries carry no usable tags, are left as
    they are and counted separately in the summary.
    """
    from ..identify import musicbrainz as mbq
    from ..tagging.partial import read_genre, write_genre

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    items = [
        (t.mb_track_id, t.mb_release_group_id, Path(t.path))
        for t in tracks
        if not t.protected
        and (t.mb_track_id or t.mb_release_group_id)
        and Path(t.path).exists()
    ]
    if ctx:
        ctx.progress(0, len(items))

    filled = 0
    had_genre = 0
    no_data = 0
    for i, (mb_track_id, mb_rg_id, p) in enumerate(items, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(items), item=p.name)
        try:
            if read_genre(p):
                had_genre += 1
                continue
            # The network fetch happens outside the file lock (like fetch_covers):
            # the derived genres depend only on MusicBrainz, not on file state, so
            # there is no read-then-write pair to protect until the actual save.
            genres: list[str] = []
            if mb_track_id:
                try:
                    rec = mbq.fetch_recording(mb_track_id)
                    genres = mbq.derive_genres(rec.get("tag-list") or [])
                except Exception:
                    log.exception("fix-genres: recording fetch failed for %s", p)
            if not genres and mb_rg_id:
                try:
                    rg = mbq.fetch_release_group(mb_rg_id)
                    genres = mbq.derive_genres(rg.get("tag-list") or [])
                except Exception:
                    log.exception("fix-genres: release-group fetch failed for %s", p)
            if not genres:
                no_data += 1
                continue
            with filelock.path_lock(p):
                write_genre(p, genres)
            filled += 1
        except Exception:
            log.exception("fix-genres: failed for %s", p)
    if ctx:
        ctx.log(
            f"Genres filled for {filled}/{len(items)} track(s) "
            f"({had_genre} already had a genre, {no_data} had no MusicBrainz genre available)"
        )
    return {"processed": len(items), "tagged": filled}


# ---------------------------------------------------------------------------
# Duplicate finder (report-only)
# ---------------------------------------------------------------------------


def find_duplicates(folder_id: int, ctx=None) -> dict:
    """Report likely duplicate tracks. Never deletes anything.

    Two signals, checked in order of confidence:
    1. identical MusicBrainz recording IDs;
    2. identical normalized (artist, title) with durations within 3 seconds.
    """
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    tracks = [t for t in tracks if Path(t.path).exists()]

    def _norm(v: str | None) -> str:
        return re.sub(r"\s+", " ", (v or "").strip().lower())

    by_mbid: dict[str, list[Track]] = {}
    by_tags: dict[tuple[str, str], list[Track]] = {}
    if ctx:
        ctx.progress(0, len(tracks))
    for i, t in enumerate(tracks, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(tracks), item=Path(t.path).name)
        if t.mb_track_id:
            by_mbid.setdefault(t.mb_track_id, []).append(t)
        if t.title and t.artist:
            by_tags.setdefault((_norm(t.artist), _norm(t.title)), []).append(t)

    groups: list[list[Track]] = [g for g in by_mbid.values() if len(g) > 1]
    seen_paths = {t.path for g in groups for t in g}
    for g in by_tags.values():
        cand = [t for t in g if t.path not in seen_paths]
        if len(cand) < 2:
            continue
        # Same artist/title is only a duplicate when durations agree too —
        # live takes and remixes routinely share a title.
        cand.sort(key=lambda t: t.duration or 0)
        cluster: list[Track] = [cand[0]]
        for t in cand[1:]:
            if abs((t.duration or 0) - (cluster[-1].duration or 0)) <= 3:
                cluster.append(t)
            else:
                if len(cluster) > 1:
                    groups.append(cluster)
                cluster = [t]
        if len(cluster) > 1:
            groups.append(cluster)

    files = sum(len(g) for g in groups)
    if ctx:
        for g in groups[:50]:
            ctx.log(f"duplicate group ({len(g)}): " + " | ".join(t.path for t in g))
        ctx.log(f"Found {len(groups)} duplicate group(s) covering {files} file(s) — report only, nothing deleted")
    log.info("find_duplicates(%d): %d groups", folder_id, len(groups))
    return {"groups": len(groups), "files": files}


# ---------------------------------------------------------------------------
# Junk pruning
# ---------------------------------------------------------------------------

# Conservative on purpose: only files that are unambiguously OS/transfer litter.
_JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini", "albumartsmall.jpg"}
_JUNK_SUFFIXES = {".tmp", ".part", ".crdownload"}


def _find_dead_folders(lib_root: Path, qroot: Path | None = None, ctx=None) -> list[Path]:
    """Return directories under ``lib_root`` that hold leftover files but no
    audio anywhere below them (a ``cover.jpg`` / orphan ``.lrc`` left by a manual
    move). Logs each when ``ctx`` is given; skips the quarantine root. Shared by
    ``prune_library`` (report) and ``cleanup_library`` (quarantine)."""
    from ..ingest.pipeline import SUPPORTED_EXTS

    dead: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(lib_root):
        d = Path(dirpath)
        if d == lib_root:
            continue
        if qroot is not None and _is_under(d, qroot):
            dirnames[:] = []
            continue
        subtree_files: list[str] = []
        subtree_has_audio = False
        for _dp, _dn, fns in os.walk(d):
            for fn in fns:
                subtree_files.append(fn)
                if Path(fn).suffix.lower() in SUPPORTED_EXTS:
                    subtree_has_audio = True
        if subtree_files and not subtree_has_audio:
            if ctx:
                sample = ", ".join(sorted(set(subtree_files))[:5])
                ctx.log(f"dead folder (no audio below, leftover files: {sample}): {d}")
            dead.append(d)
            dirnames[:] = []  # don't also descend into this dead subtree
    return dead


# "Disc N"/"CD 1"-style subfolder names, used by the orphan-disc report below.
_DISC_RE = re.compile(r"^(?:disc|cd|disk)\s*0*(\d+)$", re.IGNORECASE)


def _report_dead_folders(lib_root: Path, ctx=None) -> int:
    """Report (never delete) two classes of suspicious directory:

    * folders with no audio anywhere below them but leftover files (via
      :func:`_find_dead_folders`);
    * album folders that contain only ``Disc NN`` subfolders with disc 1
      absent — the orphan-disc symptom (the real fix is finding the missing
      discs via ``find_missing_tracks``).

    Report-only: deleting these is a judgement call the maintainer should make.
    """
    from ..ingest.pipeline import SUPPORTED_EXTS

    reported = len(_find_dead_folders(lib_root, ctx=ctx))
    for dirpath, dirnames, filenames in os.walk(lib_root):
        d = Path(dirpath)
        if d == lib_root:
            continue
        disc_subs = [c for c in dirnames if _DISC_RE.match(c)]
        direct_audio = any(Path(fn).suffix.lower() in SUPPORTED_EXTS for fn in filenames)
        if disc_subs and len(disc_subs) == len(dirnames) and not direct_audio:
            nums = {int(m.group(1)) for m in (_DISC_RE.match(c) for c in disc_subs) if m}
            if nums and 1 not in nums:
                if ctx:
                    ctx.log(f"orphan disc(s) — disc 1 missing under {d}: discs {sorted(nums)}")
                reported += 1
    return reported


def prune_library(folder_id: int, ctx=None) -> dict:
    """Delete junk files (Thumbs.db, .DS_Store, *.tmp …) and then any
    completely empty directories. Audio files are never candidates. Also
    reports (never deletes) dead folders and orphan-disc album folders."""
    from .organizer import _prune_empty_dirs

    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"junk_removed": 0, "dirs_removed": 0}
        lib_root = Path(folder.path)

    junk: list[Path] = []
    all_dirs: set[Path] = set()
    for dirpath, _dirnames, filenames in os.walk(lib_root):
        d = Path(dirpath)
        all_dirs.add(d)
        for fn in filenames:
            if fn.lower() in _JUNK_NAMES or Path(fn).suffix.lower() in _JUNK_SUFFIXES:
                junk.append(d / fn)

    if ctx:
        ctx.progress(0, len(junk) or 1)
    removed = 0
    for i, f in enumerate(junk, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(junk), item=f.name)
        try:
            f.unlink()
            removed += 1
            if ctx:
                ctx.log(f"deleted {f}")
        except OSError as e:
            if ctx:
                ctx.log(f"could not delete {f}: {e}")

    dirs_removed = _prune_empty_dirs(all_dirs, lib_root)
    dead_reported = _report_dead_folders(lib_root, ctx)
    if ctx:
        ctx.progress(len(junk) or 1, len(junk) or 1)
        ctx.log(
            f"Removed {removed} junk file(s) and {dirs_removed} empty dir(s); "
            f"{dead_reported} dead/orphan-disc folder(s) reported (not deleted)"
        )
    log.info(
        "prune_library(%d): %d junk, %d dirs, %d dead reported",
        folder_id, removed, dirs_removed, dead_reported,
    )
    return {"junk_removed": removed, "dirs_removed": dirs_removed, "dead_reported": dead_reported}


# ---------------------------------------------------------------------------
# Cleanup: merge edition-suffix twin folders, dedupe covers, quarantine dead
# folders/leftovers. Report-only by default; the apply variant moves files but
# NEVER deletes anything — leftovers go to a quarantine (trash) directory.
# ---------------------------------------------------------------------------

_QUARANTINE_DIRNAME = ".dragontag-trash"

# Image files that plausibly hold album cover art (case-insensitive), including
# duplicate spellings ("cover (1).jpg", "Folder.png", "front.jpeg").
_COVER_RE = re.compile(
    r"^(cover|folder|front|album ?art)(\s*\(\d+\))?\.(jpe?g|png)$", re.IGNORECASE
)


def _is_under(p: Path, root: Path) -> bool:
    """True if ``p`` is ``root`` or lives beneath it."""
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _quarantine_root(lib_root: Path) -> Path:
    from ..config import settings

    q = (settings().quarantine_path or "").strip()
    return Path(q) if q else lib_root / _QUARANTINE_DIRNAME


def _skip_trash(parent: Path, dirnames: list[str], qroot: Path) -> None:
    """In-place prune ``dirnames`` of the quarantine root and any .dragontag-trash."""
    dirnames[:] = [
        dn for dn in dirnames
        if not dn.startswith(_QUARANTINE_DIRNAME) and not _is_under(parent / dn, qroot)
    ]


def _count_audio(d: Path) -> int:
    from ..ingest.pipeline import SUPPORTED_EXTS

    n = 0
    for _dp, _dn, fns in os.walk(d):
        n += sum(1 for f in fns if Path(f).suffix.lower() in SUPPORTED_EXTS)
    return n


def _quarantine_file(
    f: Path, lib_root: Path, qroot: Path, run_ts: str, ctx=None
) -> bool:
    """Move non-audio ``f`` into ``qroot/<run_ts>/<rel-to-lib_root>`` (unique on
    collision). Audio is never quarantined. Returns True when moved."""
    from ..ingest.pipeline import SUPPORTED_EXTS

    if f.suffix.lower() in SUPPORTED_EXTS:
        return False  # invariant: audio is never trashed
    try:
        dest = qroot / run_ts / f.relative_to(lib_root)
    except ValueError:
        dest = qroot / run_ts / f.name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest = unique_path(dest)
        with filelock.path_lock(f):
            res = _safe_move(f, dest, overwrite=False)
    except OSError:
        log.exception("cleanup: quarantine failed for %s", f)
        return False
    if not res.moved:
        if ctx:
            ctx.log(f"cleanup: could not quarantine {f} (conflict)")
        return False
    return True


def _find_cover(d: Path) -> Path | None:
    """The canonical ``cover.jpg`` in ``d`` (case-insensitive), if present."""
    try:
        for entry in os.scandir(d):
            if entry.is_file() and entry.name.lower() == "cover.jpg":
                return Path(entry.path)
    except OSError:
        pass
    return None


def _cover_images(d: Path) -> list[Path]:
    """Cover-art candidate images directly in ``d`` (not recursive)."""
    out: list[Path] = []
    try:
        for entry in os.scandir(d):
            if entry.is_file() and _COVER_RE.match(entry.name):
                out.append(Path(entry.path))
    except OSError:
        pass
    return out


def _dedupe_covers_in_dir(
    d: Path, lib_root: Path, qroot: Path, run_ts: str, apply: bool, ctx=None
) -> int:
    """Keep one canonical ``cover.jpg`` in ``d`` (prefer an existing one, else
    promote the widest candidate) and quarantine the rest. Returns the number of
    duplicate images removed (quarantined). No-op when 0/1 candidates exist."""
    imgs = _cover_images(d)
    if len(imgs) < 2:
        return 0
    canonical = _find_cover(d)
    promoted_src: Path | None = None
    if canonical is None:
        # Promote the widest candidate to cover.jpg. Track its original path so
        # the quarantine loop skips it by identity (it may have just moved).
        promoted_src = max(imgs, key=lambda p: (_image_width(p) or 0, p.name))
        canonical = promoted_src
        target = d / "cover.jpg"
        if apply and promoted_src != target:
            try:
                with filelock.path_lock(promoted_src):
                    res = _safe_move(promoted_src, target, overwrite=False)
                if res.moved:
                    canonical = target
            except OSError:
                log.exception("cleanup: cover promote failed for %s", promoted_src)
        elif not apply:
            ctx and ctx.log(f"would promote {promoted_src.name} -> cover.jpg in {d}")
    removed = 0
    for img in imgs:
        if img == promoted_src or img.resolve() == canonical.resolve():
            continue
        if apply:
            if _quarantine_file(img, lib_root, qroot, run_ts, ctx):
                removed += 1
        else:
            ctx and ctx.log(f"would quarantine duplicate cover {img}")
            removed += 1
    return removed


def _merge_twin_folder(
    s, loser: Path, target: Path, lib_root: Path, qroot: Path, run_ts: str,
    pathmap: dict, apply: bool, source_dirs: set[Path], ctx=None,
) -> dict:
    """Move every file out of ``loser`` into ``target``: audio preserves its
    relative sub-path, the loser's cover art is elected against the target's
    (the wider ``cover.jpg`` wins), and everything else is quarantined. Track
    rows are repointed and committed per move. Returns per-loser counters."""
    from ..ingest.pipeline import SUPPORTED_EXTS

    counts = {"audio_moved": 0, "quarantined": 0, "skipped_protected": 0,
              "conflicts": 0, "covers_deduped": 0}
    loser_images: list[Path] = []
    for dp, _dn, fns in os.walk(loser):
        for fn in fns:
            if ctx:
                ctx.check_cancelled()
            src = Path(dp) / fn
            ext = src.suffix.lower()
            if ext in SUPPORTED_EXTS:
                t = pathmap.get(str(src))
                if t is not None and t.protected:
                    counts["skipped_protected"] += 1
                    ctx and ctx.log(f"cleanup: skip protected {src}")
                    continue
                dest = target / src.relative_to(loser)
                if not apply:
                    ctx and ctx.log(f"would move {src} -> {dest}")
                    counts["audio_moved"] += 1
                    continue
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with filelock.path_lock(src):
                        res = _safe_move(src, dest, overwrite=False)
                        if res.conflict:
                            counts["conflicts"] += 1
                            dest = unique_path(dest)
                            res = _safe_move(src, dest, overwrite=False)
                        if res.moved:
                            _move_lyric_sidecar(src, dest)
                except OSError:
                    log.exception("cleanup: audio move failed for %s", src)
                    continue
                if not res.moved:
                    ctx and ctx.log(f"cleanup: could not move {src} -> {dest}")
                    continue
                _update_track_path(s, str(src), str(dest))
                source_dirs.add(src.parent)
                s.commit()
                counts["audio_moved"] += 1
            elif _COVER_RE.match(fn):
                loser_images.append(src)  # elected after the walk
            else:
                if apply:
                    if _quarantine_file(src, lib_root, qroot, run_ts, ctx):
                        counts["quarantined"] += 1
                else:
                    ctx and ctx.log(f"would quarantine {src}")
                    counts["quarantined"] += 1

    # Cover election: keep the widest cover.jpg between the loser and the target;
    # quarantine every other loser image (a duplicate or a losing candidate).
    if loser_images:
        best = max(loser_images, key=lambda p: (_image_width(p) or 0, p.name))
        target_cover = _find_cover(target)
        promote = target_cover is None or (
            (_image_width(best) or 0) > (_image_width(target_cover) or 0)
        )
        if not apply:
            ctx and ctx.log(
                f"would elect cover for {target.name} "
                f"({'loser' if promote else 'target'} wins)"
            )
            # report count comes from the per-image loop below (avoid double count)
        elif promote:
            # Quarantine the target's narrower cover (if any), then move ours in.
            if target_cover is not None and _quarantine_file(
                target_cover, lib_root, qroot, run_ts, ctx
            ):
                counts["covers_deduped"] += 1
            try:
                with filelock.path_lock(best):
                    _safe_move(best, target / "cover.jpg", overwrite=False)
            except OSError:
                log.exception("cleanup: cover promote failed for %s", best)
        # Quarantine the remaining loser images (all but a promoted `best`).
        # Cover counting happens here only in apply mode; in report mode the
        # loser's images haven't moved, so the per-folder cover dedupe pass
        # (_dedupe_covers_in_dir) reports them instead — counting here too would
        # double-count.
        for img in loser_images:
            if promote and img == best:
                continue
            if apply and _quarantine_file(img, lib_root, qroot, run_ts, ctx):
                counts["covers_deduped"] += 1
            elif not apply:
                ctx and ctx.log(f"would relocate/quarantine cover {img}")
    return counts


def cleanup_library(folder_id: int, ctx=None, *, apply: bool = False) -> dict:
    """Merge edition-suffix twin album folders, dedupe cover art and quarantine
    dead folders/leftovers. **Report-only by default**; ``apply=True`` moves
    files and quarantines leftovers into ``<library>/.dragontag-trash`` (or the
    configured ``quarantine_path``) but never deletes anything.

    Twin folders (``Afraid`` / ``Afraid - Single`` / ``Afraid (Deluxe)``) are
    merged into one elected target, preserving each file's ``Disc N`` sub-path;
    tag values are left untouched (``check_album_consistency`` /
    ``fix_album_splits`` own tag agreement). Protected tracks are never moved.
    """
    mode = "apply" if apply else "report"
    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"mode": mode, "twin_groups": 0}
        lib_root = Path(folder.path)
        pathmap = {
            t.path: t
            for t in s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
        }

    qroot = _quarantine_root(lib_root)
    run_ts = now_utc().strftime("%Y%m%dT%H%M%SZ")
    result = {
        "mode": mode, "twin_groups": 0, "audio_moved": 0, "quarantined": 0,
        "covers_deduped": 0, "dead_folders": 0, "dirs_removed": 0,
        "skipped_protected": 0, "conflicts": 0,
    }
    source_dirs: set[Path] = set()
    quarantined_anything = False

    if not lib_root.exists():
        ctx and ctx.log(f"library root does not exist: {lib_root}")
        return result

    # 1. Twin-folder merge, grouped per artist directory.
    with session() as s:
        for artist_dir in sorted(p for p in lib_root.iterdir() if p.is_dir()):
            if _is_under(artist_dir, qroot) or artist_dir.name.startswith(_QUARANTINE_DIRNAME):
                continue
            if ctx:
                ctx.check_cancelled()
            albums = [p for p in artist_dir.iterdir() if p.is_dir()]
            groups: dict[str, list[Path]] = {}
            for a in albums:
                key = album_fold_key(a.name)
                if key:
                    groups.setdefault(key, []).append(a)
            for key, group in groups.items():
                if len(group) < 2:
                    continue
                result["twin_groups"] += 1
                target = sorted(
                    group,
                    key=lambda d: (-_count_audio(d), strip_edition_suffixes(d.name) != d.name, d.name),
                )[0]
                if ctx:
                    ctx.log(f"twin album group -> keep {target.name}: "
                            f"{[d.name for d in group if d != target]}")
                for loser in group:
                    if loser == target:
                        continue
                    c = _merge_twin_folder(
                        s, loser, target, lib_root, qroot, run_ts, pathmap,
                        apply, source_dirs, ctx,
                    )
                    for k in ("audio_moved", "quarantined", "skipped_protected",
                              "conflicts", "covers_deduped"):
                        result[k] += c[k]
                    if apply and c["quarantined"]:
                        quarantined_anything = True

    # 2. Cover dedupe across every album directory (post-merge).
    for dp, dirnames, _fns in os.walk(lib_root):
        d = Path(dp)
        _skip_trash(d, dirnames, qroot)
        if d == lib_root:
            continue
        n = _dedupe_covers_in_dir(d, lib_root, qroot, run_ts, apply, ctx)
        result["covers_deduped"] += n
        if apply and n:
            quarantined_anything = True

    # 3. Dead folders (no audio anywhere below): quarantine their leftovers.
    dead = _find_dead_folders(lib_root, qroot, ctx)
    result["dead_folders"] = len(dead)
    for dd in dead:
        for dp, _dn, fns in os.walk(dd):
            for fn in fns:
                src = Path(dp) / fn
                if apply:
                    if _quarantine_file(src, lib_root, qroot, run_ts, ctx):
                        result["quarantined"] += 1
                        quarantined_anything = True
                else:
                    ctx and ctx.log(f"would quarantine (dead folder) {src}")
                    result["quarantined"] += 1
        source_dirs.add(dd)

    # 4. Prune emptied directories (apply only).
    if apply and source_dirs:
        from .organizer import _prune_empty_dirs
        result["dirs_removed"] = _prune_empty_dirs(source_dirs, lib_root)

    # 5. Keep the quarantine root out of future scans/ingests.
    if quarantined_anything:
        from ..config import settings, store
        qstr = str(qroot)
        if qstr not in settings().scan_exclude_dirs:
            def _add(cur):
                dirs = list(cur.scan_exclude_dirs)
                if qstr not in dirs:
                    dirs.append(qstr)
                return {"scan_exclude_dirs": dirs}
            store().transact(_add)

    if ctx:
        summary = (
            f"Cleanup [{mode}]: {result['twin_groups']} twin group(s), "
            f"{result['audio_moved']} audio moved, {result['covers_deduped']} cover(s) deduped, "
            f"{result['dead_folders']} dead folder(s), {result['quarantined']} file(s) quarantined, "
            f"{result['dirs_removed']} dir(s) pruned, {result['skipped_protected']} protected skipped"
        )
        if quarantined_anything:
            summary += f" — quarantine dir: {qroot}"
        ctx.log(summary)
    log.info("cleanup_library(%d)[%s]: %s", folder_id, mode, result)
    return result


# ---------------------------------------------------------------------------
# Tag validation (report-only)
# ---------------------------------------------------------------------------

_MOJIBAKE_RE = re.compile(r"Ã[\x80-\xbf]|â€|Ð[\x80-\xbf]|ï¿½")


def validate_tags(folder_id: int, ctx=None) -> dict:
    """Report tag-health problems without changing anything: missing core
    fields, mojibake-looking values, and track numbers above the declared
    track total."""
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    items = [t for t in tracks if Path(t.path).exists()]

    problems: list[str] = []
    seen_bad_albums: set[str] = set()  # report each suspicious album name once
    if ctx:
        ctx.progress(0, len(items))
    for i, t in enumerate(items, start=1):
        name = Path(t.path).name
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(items), item=name)
        if not t.title:
            problems.append(f"{name}: missing title")
        if not t.artist:
            problems.append(f"{name}: missing artist")
        if not t.album_artist:
            problems.append(f"{name}: missing album artist")
        for field in ("title", "artist", "album", "album_artist"):
            v = getattr(t, field) or ""
            if _MOJIBAKE_RE.search(v):
                problems.append(f"{name}: {field} looks mis-encoded: {v!r}")
        if t.track_num and t.track_total and t.track_num > t.track_total:
            problems.append(f"{name}: track {t.track_num} > track total {t.track_total}")
        if t.disc_num and t.disc_total and t.disc_num > t.disc_total:
            problems.append(f"{name}: disc {t.disc_num} > disc total {t.disc_total}")
        # Suspicious album folder names (class 9): an album that is only an
        # edition marker / punctuation with no real title, or one that
        # sanitizes to the "_" placeholder — the folder shows up as "(Deluxe)"
        # or "_" with no base album name.
        alb = (t.album or "").strip()
        if alb and alb not in seen_bad_albums:
            base = strip_edition_suffixes(alb).strip(" ()[]{}-–—_")
            if not base:
                seen_bad_albums.add(alb)
                problems.append(f"{name}: suspicious album name {alb!r} (edition marker / punctuation only)")
            elif sanitize_segment(alb) == "_":
                seen_bad_albums.add(alb)
                problems.append(f"{name}: album name {alb!r} sanitizes to the '_' placeholder")
    if ctx:
        for line in problems[:100]:
            ctx.log(line)
        ctx.log(f"Checked {len(items)} track(s): {len(problems)} problem(s) — report only")
    log.info("validate_tags(%d): %d problems", folder_id, len(problems))
    return {"checked": len(items), "problems": len(problems)}


# ---------------------------------------------------------------------------
# Action registry — single source of truth for the per-folder buttons on the
# Library page. Deliberately small: the ONE tagging pass is the ingest
# pipeline ("Retag" = bulk.enqueue_folder), everything here is either a
# single-field in-place backfill, a report, or the cleanup. The old batch
# compositions (Organize/Re-tag/Nuclear) and structural repair actions
# (fix_album_splits, unify_artist_folders, check_album_consistency,
# fix_disc_folders, normalize_filenames, reidentify) are gone — album-first
# identification and safe destination resolution in the pipeline made them
# redundant, and their unattended file-moving is how libraries got wrecked.
# ---------------------------------------------------------------------------

LIBRARY_ACTIONS: dict[str, tuple[str, str, Any]] = {
    "extract_covers": (
        "Extract embedded covers",
        "Write each album's embedded cover art out as cover.jpg in the album folder (only if none exists).",
        extract_embedded_covers),
    "fetch_covers": (
        "Fetch cover art",
        "Fetch missing cover art from the Cover Art Archive for tracks with a MusicBrainz album ID.",
        fetch_covers_for_folder),
    "fetch_lyrics": (
        "Fetch lyrics",
        "Fetch synced or plain-text lyrics from LRCLIB and embed them. Does not re-identify tracks.",
        fetch_lyrics_for_folder),
    "tag_advisories": (
        "Tag advisories",
        "Re-run the explicit-content classifier on embedded lyrics and update the advisory flag.",
        tag_advisories_for_folder),
    "fix_genres": (
        "Fix genres",
        "Backfill missing genres from MusicBrainz community tags (recording, then release-group) "
        "for tracks that have none. Only fills empty genres; never overwrites an existing one.",
        fix_genres_for_folder),
    "replaygain": (
        "Recompute ReplayGain",
        "Compute ReplayGain album + track tags per album using rsgain or loudgain (skips if neither is installed).",
        recompute_replaygain),
    "verify_integrity": (
        "Verify file integrity",
        "Read every audio file via mutagen and report any that fail to decode.",
        verify_integrity),
    "validate_tags": (
        "Validate tags",
        "Report missing core tags, mis-encoded text and impossible track/disc numbers. Report only.",
        validate_tags),
    "find_duplicates": (
        "Find duplicates",
        "Report likely duplicate tracks by MusicBrainz ID and matching artist/title/duration. Report only.",
        find_duplicates),
    "prune": (
        "Prune junk & empty folders",
        "Delete OS litter (Thumbs.db, .DS_Store, *.tmp …) and completely empty folders. Audio is never touched.",
        prune_library),
    "cleanup": (
        "Cleanup (report)",
        "Report edition-suffix twin album folders (X / X - Single / X (Deluxe)), duplicate "
        "cover art and dead folders. Report only — the apply variant on the Library page merges "
        "twins and quarantines leftovers into the trash folder; nothing is ever deleted.",
        cleanup_library),
    "find_missing_tracks": (
        "Find missing tracks",
        "Compare each album's local track count to MusicBrainz and list incomplete albums on the Incomplete tab.",
        find_missing_tracks),
}
