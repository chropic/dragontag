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
from typing import Any

from sqlmodel import select

from ..db import session
from ..models import LibraryFolder, Track
from . import filelock
from .mover import move as _safe_move
from .mover import move_lyric_sidecar as _move_lyric_sidecar

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


# ---------------------------------------------------------------------------
# Disc-folder correction
# ---------------------------------------------------------------------------


_DISC_RE = re.compile(r"^(?:disc|cd|disk)\s*0*(\d+)$", re.IGNORECASE)


def fix_disc_folders(folder_id: int, ctx=None) -> dict:
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

        if ctx:
            ctx.progress(0, len(album_dirs))
        for ai, album in enumerate(sorted(album_dirs), start=1):
            if ctx:
                ctx.check_cancelled()
                ctx.progress(ai, len(album_dirs), item=album.name)
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
                        # Conflict-safe move: refuses to overwrite a file that
                        # appears at the target during the race window, and only
                        # then do we update the DB path — so the DB never points
                        # somewhere the file didn't actually land. path_lock
                        # serializes with the other file mutators on this path.
                        with filelock.path_lock(f):
                            result = _safe_move(f, target, overwrite=False)
                        if not result.moved:
                            continue
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
            disc_nums = [int(m.group(1)) for m in (_DISC_RE.match(d.name) for d in disc_children) if m]
            disc_total = max(disc_nums) if disc_nums else len(disc_children)
            for d in disc_children:
                m = _DISC_RE.match(d.name)
                if not m:
                    continue
                n = int(m.group(1))
                # Supply every placeholder build_destination supports — a
                # "Disc {disc} of {disctotal}" template must not KeyError here.
                want = template.format(disc=n, disctotal=disc_total)
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
    if ctx:
        ctx.log(f"Renamed {renamed} disc folder(s), flattened {flattened} file(s), {errors} error(s)")
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
# Album/folder consistency checker
# ---------------------------------------------------------------------------

_EDITION_SUFFIX_RE = re.compile(
    r"\s*[\(\[]\s*(deluxe|remaster(?:ed)?|expanded|anniversary|bonus track|special|"
    r"explicit|clean|single|ep|mono|stereo)[^)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)


def _normalize_album_key(album: str | None, album_artist: str | None) -> tuple[str, str] | None:
    """Fold an (album, album_artist) pair into a deterministic grouping key.

    Used only as a fallback for tracks with no MusicBrainz release-group id —
    deterministic exact-match-after-normalization avoids any false-positive
    merges (which would be destructive), at the cost of missing matches a
    human would consider obviously "the same album" (e.g. typos).
    """
    if not album or not album_artist:
        return None
    a = _EDITION_SUFFIX_RE.sub("", album).strip().lower()
    a = re.sub(r"[^\w\s]", "", a)
    a = re.sub(r"\s+", " ", a).strip()
    artist = re.sub(r"[^\w\s]", "", album_artist.strip().lower())
    artist = re.sub(r"\s+", " ", artist).strip()
    if not a or not artist:
        return None
    return (a, artist)


def _build_album_groups(tracks: list[Track]) -> list[list[Track]]:
    """Group tracks by MB release-group id, falling back to a normalized
    (album, album_artist) match for tracks with no MB id at all."""
    by_mbid: dict[str, list[Track]] = {}
    by_normalized: dict[tuple[str, str], list[Track]] = {}
    for t in tracks:
        if t.mb_release_group_id:
            by_mbid.setdefault(t.mb_release_group_id, []).append(t)
        else:
            key = _normalize_album_key(t.album, t.album_artist)
            if key:
                by_normalized.setdefault(key, []).append(t)
    return list(by_mbid.values()) + list(by_normalized.values())


def _majority_pair(tracks: list[Track]) -> tuple[str, str]:
    """Majority ``(album, album_artist)`` pair across ``tracks``.

    The two fields are voted *jointly* — voting them independently could
    combine one track's album with another track's artist into a pair no
    track ever carried, and the whole group would then be rewritten/moved to
    that invented state. Ties are broken by the most-recently-indexed track
    carrying a tied pair (a fresher MB identification is a slightly better
    signal), then alphabetically for determinism."""
    from collections import Counter

    def pair(t: Track) -> tuple[str, str]:
        return (t.album or "", t.album_artist or "")

    counts = Counter(pair(t) for t in tracks)
    best_count = max(counts.values())
    tied = sorted(p for p, c in counts.items() if c == best_count)
    if len(tied) == 1:
        return tied[0]
    candidates = [t for t in tracks if pair(t) in tied]
    candidates.sort(key=lambda t: (t.indexed_at, pair(t)), reverse=True)
    return pair(candidates[0])


def check_album_consistency(folder_id: int, ctx=None) -> dict:
    """Detect tracks sharing a MusicBrainz release-group (or, lacking an MB
    id, a normalized album+artist match) that disagree on album/album_artist
    tags, normalize them to the majority value, and physically move outlier
    files into the resulting single canonical folder.

    Skips protected tracks entirely (neither tag-patched nor moved) and
    patches only album/album_artist via a partial write — a corrective
    metadata patch, not a re-identify, so every other tag is left untouched.
    A destination conflict from a same-path file is best-effort: the tag
    patch still applies but the physical move is skipped and logged.
    """
    from ..tagging.partial import write_basic_tags
    from .organizer import _prune_empty_dirs
    from .paths import build_destination
    from ..tagging.schema import TrackTags

    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"ok": False, "reason": "Folder not found"}
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
        lib_root = Path(folder.path)

    groups = _build_album_groups(tracks)
    groups_checked = 0
    tracks_fixed = 0
    source_dirs: set[Path] = set()

    if ctx:
        ctx.progress(0, len(groups))
    with session() as s:
        for gi, group in enumerate(groups, start=1):
            if ctx:
                ctx.check_cancelled()
                ctx.progress(gi, len(groups))
            eligible = [t for t in group if not t.protected and Path(t.path).exists()]
            if len(eligible) < 2:
                continue
            groups_checked += 1
            winning_album, winning_artist = _majority_pair(eligible)
            if all(t.album == winning_album and t.album_artist == winning_artist for t in eligible):
                continue  # already consistent, nothing to do

            for t in eligible:
                if t.album == winning_album and t.album_artist == winning_artist:
                    continue
                p = Path(t.path)
                try:
                    # path_lock: in-place mutator, same rule as the pipeline /
                    # organizer / revert.
                    with filelock.path_lock(p):
                        write_basic_tags(
                            p, title=None, artist=None,
                            album=winning_album, album_artist=winning_artist,
                            track=None, track_total=None, disc=None, disc_total=None,
                        )
                except Exception:
                    log.exception("album-consistency: tag patch failed for %s", p)
                    continue

                shim = TrackTags(
                    title=t.title, artist_display=t.artist,
                    album=winning_album, album_artist_display=winning_artist,
                    track=t.track_num, track_total=t.track_total,
                    disc=t.disc_num, disc_total=t.disc_total,
                )
                try:
                    dest = build_destination(shim, p.suffix, library_root=lib_root)
                except ValueError:
                    log.exception("album-consistency: destination computation failed for %s", p)
                    continue

                db_t = s.get(Track, t.id)
                if dest == p:
                    if db_t:
                        db_t.album, db_t.album_artist = winning_album, winning_artist
                        s.add(db_t)
                    tracks_fixed += 1
                    s.commit()
                    continue

                try:
                    with filelock.path_lock(p):
                        result = _safe_move(p, dest, overwrite=False)
                        if result.moved:
                            _move_lyric_sidecar(p, dest)
                except OSError:
                    # The tag patch is already on the file — keep the matching
                    # DB update and carry on with the rest of the group.
                    log.exception("album-consistency: move failed for %s", p)
                    if db_t:
                        db_t.album, db_t.album_artist = winning_album, winning_artist
                        s.add(db_t)
                    tracks_fixed += 1
                    s.commit()
                    continue
                if not result.moved:
                    if ctx:
                        ctx.log(f"album-consistency: destination conflict, tags patched but not moved: {p} -> {dest}")
                    if db_t:
                        db_t.album, db_t.album_artist = winning_album, winning_artist
                        s.add(db_t)
                    tracks_fixed += 1
                    s.commit()
                    continue

                _update_track_path(s, str(p), str(dest))
                moved_t = s.exec(select(Track).where(Track.path == str(dest))).first()
                if moved_t:
                    moved_t.album, moved_t.album_artist = winning_album, winning_artist
                    s.add(moved_t)
                source_dirs.add(p.parent)
                tracks_fixed += 1
                # Commit per track: the file was already physically moved, so a
                # failure later in the loop must not roll this row back to a
                # path that no longer exists on disk.
                s.commit()
                if ctx:
                    ctx.log(f"moved {p.name}: {p.parent} -> {dest.parent}")
        s.commit()

    folders_merged = _prune_empty_dirs(source_dirs, lib_root) if source_dirs else 0
    summary = {"groups_checked": groups_checked, "tracks_fixed": tracks_fixed, "folders_merged": folders_merged}
    log.info("check_album_consistency(%d): %s", folder_id, summary)
    if ctx:
        ctx.log(f"Checked {groups_checked} group(s), fixed {tracks_fixed} track(s), merged/pruned {folders_merged} empty folder(s)")
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


def prune_library(folder_id: int, ctx=None) -> dict:
    """Delete junk files (Thumbs.db, .DS_Store, *.tmp …) and then any
    completely empty directories. Audio files are never candidates."""
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
    if ctx:
        ctx.progress(len(junk) or 1, len(junk) or 1)
        ctx.log(f"Removed {removed} junk file(s) and {dirs_removed} empty dir(s)")
    log.info("prune_library(%d): %d junk, %d dirs", folder_id, removed, dirs_removed)
    return {"junk_removed": removed, "dirs_removed": dirs_removed}


# ---------------------------------------------------------------------------
# Filename normalization
# ---------------------------------------------------------------------------


def normalize_filenames(folder_id: int, ctx=None) -> dict:
    """Normalize file names without re-tagging: lowercase extensions
    (``.FLAC`` → ``.flac``), strip trailing dots/spaces from the stem, collapse
    runs of whitespace. Updates ``Track.path`` for every rename."""
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    items = [t for t in tracks if Path(t.path).exists()]

    renamed = 0
    errors = 0
    if ctx:
        ctx.progress(0, len(items))
    with session() as s:
        for i, t in enumerate(items, start=1):
            p = Path(t.path)
            if ctx:
                ctx.check_cancelled()
                ctx.progress(i, len(items), item=p.name)
            stem = re.sub(r"\s+", " ", p.stem).strip(" .")
            new_name = (stem or p.stem) + p.suffix.lower()
            if new_name == p.name:
                continue
            target = p.with_name(new_name)
            try:
                # path_lock: renames are file moves — serialize with the other
                # mutators (worker / organizer / revert) on this path.
                with filelock.path_lock(p):
                    if target.exists() and target != p:
                        # Case-only renames collide on case-insensitive filesystems;
                        # go through a temp name.
                        if str(target).lower() == str(p).lower():
                            tmp = p.with_name(new_name + ".dgtmp")
                            p.rename(tmp)
                            tmp.rename(target)
                        else:
                            if ctx:
                                ctx.log(f"skip (target exists): {p.name} -> {new_name}")
                            continue
                    else:
                        # Conflict-safe move: a plain rename() overwrites the target
                        # on POSIX, so a file racing into ``target`` after our
                        # exists() check above would be silently destroyed. Refuse
                        # to overwrite and skip instead.
                        result = _safe_move(p, target, overwrite=False)
                        if not result.moved:
                            if ctx:
                                ctx.log(f"skip (target appeared): {p.name} -> {new_name}")
                            continue
                _update_track_path(s, str(p), str(target))
                if _move_lyric_sidecar(p, target) and ctx:
                    ctx.log(f"renamed lyric sidecar for {new_name}")
                renamed += 1
                if ctx:
                    ctx.log(f"renamed {p.name} -> {new_name}")
            except OSError as e:
                errors += 1
                if ctx:
                    ctx.log(f"failed {p.name}: {e}")
        s.commit()
    if ctx:
        ctx.log(f"Renamed {renamed} file(s), {errors} error(s)")
    log.info("normalize_filenames(%d): %d renamed", folder_id, renamed)
    return {"renamed": renamed, "errors": errors}


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
    if ctx:
        for line in problems[:100]:
            ctx.log(line)
        ctx.log(f"Checked {len(items)} track(s): {len(problems)} problem(s) — report only")
    log.info("validate_tags(%d): %d problems", folder_id, len(problems))
    return {"checked": len(items), "problems": len(problems)}


# ---------------------------------------------------------------------------
# Action registry — single source of truth for buttons, multi-select chains
# and the batch operations. Order here is the canonical execution order.
# ---------------------------------------------------------------------------

LIBRARY_ACTIONS: dict[str, tuple[str, str, Any]] = {
    "fix_disc_folders": (
        "Fix disc folders",
        "Normalize disc-N subfolders to the configured multi-disc folder template (or flatten single-disc trees).",
        fix_disc_folders),
    "normalize_filenames": (
        "Normalize filenames",
        "Lowercase extensions (.FLAC → .flac), strip trailing dots/spaces and collapse double spaces. No re-tagging.",
        normalize_filenames),
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
    "find_missing_tracks": (
        "Find missing tracks",
        "Compare each album's local track count to MusicBrainz and list incomplete albums on the Incomplete tab.",
        find_missing_tracks),
    "check_album_consistency": (
        "Fix album/folder consistency",
        "Detect tracks sharing a MusicBrainz release-group (or matching album+artist) "
        "with inconsistent album/album_artist tags, normalize to the majority value, "
        "and move outlier files into the resulting single folder. Skips protected tracks; "
        "patches tags via a partial write (other fields untouched). Best-effort: a tag "
        "patch always applies, but a physical move is skipped (and logged) if a same-path "
        "file already exists for an unrelated reason.",
        check_album_consistency),
}

# Batch compositions (keys into LIBRARY_ACTIONS, executed in order). "organize"
# itself is prepended by the route layer because it lives in organizer.py.
BATCH_ORGANIZE = [
    "fix_disc_folders", "normalize_filenames", "check_album_consistency",
    "extract_covers", "prune", "find_duplicates", "find_missing_tracks",
]
BATCH_RETAG = ["validate_tags", "tag_advisories", "replaygain"]

# Nuclear option: the full identify -> tag -> move pipeline runs first (added
# manually by the route layer, since it lives in ingest.bulk), then this list
# in order. Mirrors the logical dependency chain: tags/covers/lyrics/advisory
# data must exist before disc/filename cleanup, which must happen before
# ReplayGain (per-file loudness) and the report-only/cleanup passes.
BATCH_NUCLEAR = [
    "validate_tags", "fetch_covers", "fetch_lyrics", "tag_advisories",
    "fix_disc_folders", "normalize_filenames", "check_album_consistency",
    "extract_covers", "replaygain", "find_duplicates", "find_missing_tracks", "prune",
    "verify_integrity",
]


def build_chain_steps(action_keys: list[str], folder_id: int) -> list[tuple[str, Any]]:
    """Map registry keys to ``(label, fn)`` steps bound to ``folder_id``,
    ready for ``tasks.run_chain``. Unknown keys are skipped."""
    steps: list[tuple[str, Any]] = []
    for key in action_keys:
        if key not in LIBRARY_ACTIONS:
            continue
        label, _desc, fn = LIBRARY_ACTIONS[key]
        steps.append((label, (lambda f: lambda ctx: f(folder_id, ctx=ctx))(fn)))
    return steps
