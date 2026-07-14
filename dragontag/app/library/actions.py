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
                        # Commit per move: the file is already on disk at the
                        # new path, so a later cancel/exception must not roll
                        # this row back to a path that no longer exists.
                        s.commit()
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
                    # Commit per rename: the directory already moved on disk,
                    # so a later cancel/exception must not discard these
                    # path updates and leave the DB pointing into the old dir.
                    s.commit()
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

def _normalize_album_key(album: str | None, album_artist: str | None) -> tuple[str, str] | None:
    """Fold an (album, album_artist) pair into a deterministic grouping key.

    Used only as a fallback for tracks with no MusicBrainz release-group id —
    deterministic exact-match-after-normalization avoids any false-positive
    merges (which would be destructive), at the cost of missing matches a
    human would consider obviously "the same album" (e.g. typos). The same
    quote/dash/NFKC fold as the artist key is applied so case- and
    punctuation-only album variants collapse together.
    """
    if not album or not album_artist:
        return None
    a = fold_text(strip_edition_suffixes(album))
    a = re.sub(r"[^\w\s]", "", a)
    a = re.sub(r"\s+", " ", a).strip()
    artist = re.sub(r"[^\w\s]", "", fold_text(album_artist))
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


def _normalize_track_to_pair(
    s,
    t: Track,
    winning_album: str | None,
    winning_artist: str,
    lib_root: Path,
    source_dirs: set[Path],
    ctx=None,
    log_prefix: str = "album-consistency",
) -> bool:
    """Patch one track's album/album_artist to the winning value and move it
    into the canonical folder. Shared by ``check_album_consistency``, the
    offline fallback of ``fix_album_splits`` and ``unify_artist_folders``.

    ``winning_album`` may be ``None``, meaning "leave this track's own album
    alone and only unify the album artist" — the album tag is not written and
    the track keeps its existing album for the destination computation. This
    is what ``unify_artist_folders`` uses: it moves every album under a single
    artist folder without collapsing distinct albums together.

    Returns True when the track was fixed (tag patch applied, move
    best-effort), False when nothing could be changed. Commits per track —
    the file is already mutated on disk, so a later cancel/exception must not
    roll the row back to a stale path.
    """
    from ..tagging.partial import write_basic_tags
    from ..tagging.schema import TrackTags
    from .paths import build_destination

    # The album value we record/target: the winning album, or the track's own
    # album when the caller only means to unify the artist.
    effective_album = winning_album if winning_album is not None else t.album

    p = Path(t.path)
    try:
        # path_lock: in-place mutator, same rule as the pipeline /
        # organizer / revert. album=None leaves the album tag untouched.
        with filelock.path_lock(p):
            write_basic_tags(
                p, title=None, artist=None,
                album=winning_album, album_artist=winning_artist,
                track=None, track_total=None, disc=None, disc_total=None,
            )
    except Exception:
        log.exception("%s: tag patch failed for %s", log_prefix, p)
        return False

    shim = TrackTags(
        title=t.title, artist_display=t.artist,
        album=effective_album, album_artist_display=winning_artist,
        track=t.track_num, track_total=t.track_total,
        disc=t.disc_num, disc_total=t.disc_total,
    )
    try:
        dest = build_destination(shim, p.suffix, library_root=lib_root)
    except ValueError:
        log.exception("%s: destination computation failed for %s", log_prefix, p)
        return False

    db_t = s.get(Track, t.id)
    if dest == p:
        if db_t:
            db_t.album, db_t.album_artist = effective_album, winning_artist
            s.add(db_t)
        s.commit()
        return True

    try:
        with filelock.path_lock(p):
            result = _safe_move(p, dest, overwrite=False)
            if result.moved:
                _move_lyric_sidecar(p, dest)
    except OSError:
        # The tag patch is already on the file — keep the matching
        # DB update and carry on with the rest of the group.
        log.exception("%s: move failed for %s", log_prefix, p)
        if db_t:
            db_t.album, db_t.album_artist = effective_album, winning_artist
            s.add(db_t)
        s.commit()
        return True
    if not result.moved:
        if ctx:
            ctx.log(f"{log_prefix}: destination conflict, tags patched but not moved: {p} -> {dest}")
        if db_t:
            db_t.album, db_t.album_artist = effective_album, winning_artist
            s.add(db_t)
        s.commit()
        return True

    _update_track_path(s, str(p), str(dest))
    moved_t = s.exec(select(Track).where(Track.path == str(dest))).first()
    if moved_t:
        moved_t.album, moved_t.album_artist = effective_album, winning_artist
        s.add(moved_t)
    source_dirs.add(p.parent)
    s.commit()
    if ctx:
        ctx.log(f"moved {p.name}: {p.parent} -> {dest.parent}")
    return True


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
    from .organizer import _prune_empty_dirs

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
                if _normalize_track_to_pair(
                    s, t, winning_album, winning_artist, lib_root, source_dirs, ctx
                ):
                    tracks_fixed += 1
        s.commit()

    folders_merged = _prune_empty_dirs(source_dirs, lib_root) if source_dirs else 0
    summary = {"groups_checked": groups_checked, "tracks_fixed": tracks_fixed, "folders_merged": folders_merged}
    log.info("check_album_consistency(%d): %s", folder_id, summary)
    if ctx:
        ctx.log(f"Checked {groups_checked} group(s), fixed {tracks_fixed} track(s), merged/pruned {folders_merged} empty folder(s)")
    return summary


# ---------------------------------------------------------------------------
# Artist-folder unification: one folder per artist across every album
# ---------------------------------------------------------------------------


def _elect_canonical_artist(tracks: list[Track]) -> str:
    """Elect the canonical album-artist spelling for a group by majority vote.

    Votes over the *raw* ``album_artist`` (falling back to ``artist``) strings
    — a pure count, deliberately *not* a "prefer capitals" heuristic:
    stylized-lowercase names (``fakemink``, ``glaive``, ``jonatan
    leandoer96``) are legitimate, and the majority of the user's own files is
    the least-surprising winner. Ties break by the most-recently-indexed track
    carrying a tied spelling, then alphabetically for determinism (mirrors
    ``_majority_pair``). Returns ``""`` when no track has a usable name.
    """
    def name(t: Track) -> str:
        return (t.album_artist or t.artist or "").strip()

    counts = Counter(name(t) for t in tracks if name(t))
    if not counts:
        return ""
    best = max(counts.values())
    tied = sorted(n for n, c in counts.items() if c == best)
    if len(tied) == 1:
        return tied[0]
    candidates = [t for t in tracks if name(t) in tied]
    candidates.sort(key=lambda t: (t.indexed_at, name(t)), reverse=True)
    return name(candidates[0])


def _group_tracks_by_artist(tracks: list[Track]) -> list[list[Track]]:
    """Group tracks that should share a single artist folder.

    Primary key is the MusicBrainz *album-artist id* — it unifies alias/credit
    variants that fold differently (``FERG``/``A$AP Ferg``, ``Cordae``/``YBN
    Cordae``, a Japanese-script credit) into one group. Tracks with no id fall
    back to the folded artist name (case/punctuation/Unicode-insensitive), and
    join an id-keyed group when their fold key matches that group's folded
    canonical name — so an id-less file lands with its id-carrying siblings.
    """
    id_groups: dict[str, list[Track]] = {}
    fold_only: dict[str, list[Track]] = {}
    for t in tracks:
        name = t.album_artist or t.artist or ""
        if not name.strip():
            continue
        aid = getattr(t, "mb_album_artist_id", None)
        if aid:
            id_groups.setdefault(aid, []).append(t)
        else:
            key = artist_fold_key(name)
            if key:
                fold_only.setdefault(key, []).append(t)

    # Fold key of each id-group's canonical name, so id-less tracks can join.
    fold_to_id_group: dict[str, list[Track]] = {}
    for group in id_groups.values():
        fk = artist_fold_key(_elect_canonical_artist(group))
        if fk:
            fold_to_id_group.setdefault(fk, group)

    groups: list[list[Track]] = list(id_groups.values())
    for key, group in fold_only.items():
        target = fold_to_id_group.get(key)
        if target is not None:
            target.extend(group)
        else:
            groups.append(group)
    return groups


def _rename_artist_dir(s, tracks: list[Track], src: Path, dst: Path, ctx=None) -> bool:
    """Rename an artist directory ``src`` → ``dst`` and re-point every
    ``Track.path`` beneath it.

    Handles the case-insensitive-mount caveat: when the per-file move degraded
    to a tag-patch (``dst`` resolves to the same inode as ``src``, differing
    only in case), rename via a temp name so the case actually changes on
    disk. Refuses when ``dst`` already exists as a genuinely distinct
    directory (a real collision — leave those files where they are and let the
    per-file merge / prune handle it). Returns True on a successful rename.
    """
    try:
        if not src.exists() or not src.is_dir():
            return False
        if not dst.exists():
            src.rename(dst)
        elif dst != src and os.path.samefile(str(src), str(dst)):
            # dst resolves to the *same inode* as src (case-insensitive mount,
            # names differ only by case): a direct rename is a no-op, so go
            # through a temp name to actually change the casing on disk.
            tmp = src.with_name(src.name + ".dgtmp")
            src.rename(tmp)
            tmp.rename(dst)
        else:
            # dst is a genuinely distinct existing directory — a real
            # collision. Leave those files where they are (the per-file move
            # / prune handles the case-sensitive merge).
            return False
    except OSError:
        return False

    prefix = str(src) + os.sep
    for t in tracks:
        if t.path.startswith(prefix):
            db_t = s.get(Track, t.id)
            if db_t:
                db_t.path = t.path.replace(str(src), str(dst), 1)
                s.add(db_t)
    s.commit()
    if ctx:
        ctx.log(f"renamed artist folder {src.name} -> {dst.name}")
    return True


def unify_artist_folders(folder_id: int, ctx=None) -> dict:
    """Collapse duplicate artist folders caused by casing / punctuation /
    alias drift into one canonical folder per artist.

    For each group of tracks that belong to the same artist — keyed by
    MusicBrainz album-artist id, else by a case/punctuation/Unicode-folded
    name — elect the majority album-artist spelling and rewrite every outlier
    track's ``album_artist`` tag to it, moving the file under the canonical
    artist folder (its album is left untouched, so distinct albums stay
    distinct). Then rename any leftover artist directory that differs from the
    canonical only by case (case-insensitive mounts) and prune the emptied
    source folders.

    Skips protected tracks entirely; every file mutate/move holds
    ``path_lock`` and commits per track (via ``_normalize_track_to_pair``).
    Offline — no network.
    """
    from .organizer import _prune_empty_dirs

    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"ok": False, "reason": "Folder not found"}
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
        lib_root = Path(folder.path)

    groups = _group_tracks_by_artist(tracks)
    groups_checked = 0
    tracks_fixed = 0
    source_dirs: set[Path] = set()
    canonical_names: set[str] = set()

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
            canonical = _elect_canonical_artist(eligible)
            if not canonical:
                continue
            canonical_names.add(canonical)
            if all((t.album_artist or "") == canonical for t in eligible):
                continue  # already unified
            groups_checked += 1
            for t in eligible:
                if (t.album_artist or "") == canonical:
                    continue
                if _normalize_track_to_pair(
                    s, t, None, canonical, lib_root, source_dirs, ctx,
                    log_prefix="unify-artists",
                ):
                    tracks_fixed += 1
        s.commit()

    # Case-only directory cleanup: after the per-file moves, an artist dir may
    # still carry the loser's casing (case-insensitive mount, where the move
    # degraded to a tag patch). Rename such dirs onto the canonical spelling.
    folders_renamed = 0
    with session() as s:
        live_tracks = s.exec(
            select(Track).where(Track.library_folder_id == folder_id)
        ).all()
        for canonical in canonical_names:
            target_name = sanitize_segment(primary_artist(canonical))
            target_dir = lib_root / target_name
            target_fold = fold_text(target_name)
            try:
                entries = list(os.scandir(lib_root))
            except OSError:
                break
            for entry in entries:
                if not entry.is_dir() or entry.name == target_name:
                    continue
                if fold_text(entry.name) != target_fold:
                    continue
                src_dir = Path(entry.path)
                if _rename_artist_dir(s, live_tracks, src_dir, target_dir, ctx):
                    folders_renamed += 1
                    source_dirs.add(src_dir)

    folders_merged = _prune_empty_dirs(source_dirs, lib_root) if source_dirs else 0
    summary = {
        "groups": groups_checked,
        "tracks_fixed": tracks_fixed,
        "folders_renamed": folders_renamed,
        "folders_merged": folders_merged,
    }
    log.info("unify_artist_folders(%d): %s", folder_id, summary)
    if ctx:
        ctx.log(
            f"Unified {groups_checked} artist group(s), fixed {tracks_fixed} track(s), "
            f"renamed {folders_renamed} folder(s), merged/pruned {folders_merged} empty folder(s)"
        )
    return summary


# ---------------------------------------------------------------------------
# Album-split repair: unify every track of a release group onto one release
# ---------------------------------------------------------------------------


def _group_is_split(tracks: list[Track]) -> bool:
    """True when tracks that belong to one album disagree on any of the
    fields players group albums by (as mirrored in the DB)."""
    album_ids = {t.mb_album_id for t in tracks if t.mb_album_id}
    if len(album_ids) > 1:
        return True
    pairs = {(t.album or "", t.album_artist or "") for t in tracks}
    if len(pairs) > 1:
        return True
    totals = {t.track_total for t in tracks if t.track_total}
    return len(totals) > 1


def _elect_canonical_release(
    album_ids: set[str], group_recording_ids: set[str], ctx=None
) -> tuple[str | None, dict | None, set[str]]:
    """Pick the one release every track of the group should be tagged against.

    Fetches each candidate release once from MusicBrainz and ranks by:
    recording coverage (how many of the group's recordings the release
    actually contains — the deluxe/superset edition wins over a standard
    edition that lacks the bonus tracks), then Official status, then total
    track count, then lexicographically smallest id for determinism.

    Returns ``(release_id, release_doc, recording_ids_on_release)`` or
    ``(None, None, set())`` when no candidate could be fetched.
    """
    from ..identify import musicbrainz as mbq

    best: tuple | None = None
    for rid in sorted(album_ids):
        try:
            rel = mbq.fetch_release(rid)
        except Exception as e:
            if ctx:
                ctx.log(f"fix-splits: could not fetch release {rid}: {e}")
            continue
        recs = {
            (trk.get("recording") or {}).get("id")
            for medium in rel.get("medium-list") or []
            for trk in medium.get("track-list") or []
        } - {None}
        coverage = len(group_recording_ids & recs)
        official = rel.get("status") == "Official"
        total = mbq._release_track_total(rel) or 0
        key = (-coverage, not official, -total, rid)
        if best is None or key < best[0]:
            best = (key, rid, rel, recs)
    if best is None:
        return None, None, set()
    return best[1], best[2], best[3]


def _retag_to_canonical(
    s,
    t: Track,
    release_id: str,
    release_doc: dict,
    cover,
    lib_root: Path,
    source_dirs: set[Path],
    dest_dirs: set[Path],
    ctx=None,
) -> bool:
    """Fully re-tag one track against the canonical release and move it to
    its canonical location. Returns True when the file was rewritten."""
    from ..identify import musicbrainz as mbq
    from ..ingest.pipeline import prepare_tags
    from ..tagging.partial import read_lyrics
    from ..tagging.writers import write_tags
    from .paths import build_destination

    p = Path(t.path)
    try:
        tags = mbq.assemble_tags(
            release_id=release_id, recording_id=t.mb_track_id, rel=release_doc
        )
    except Exception as e:
        if ctx:
            ctx.log(f"fix-splits: assemble failed for {p.name}: {e}")
        return False
    prepare_tags(None, tags)
    if cover:
        tags.cover_bytes, tags.cover_mime = cover.data, cover.mime

    moved_to = p
    try:
        # One lock over the read-then-write pair *and* the move: nothing may
        # rewrite or relocate the file between reading its lyrics/art and the
        # full-frame rewrite (rule: read-then-write mutators hold path_lock).
        with filelock.path_lock(p):
            lyrics = read_lyrics(p)
            if lyrics:
                # The full writers replace the entire frame set — carry the
                # already-embedded lyrics (and the advisory derived from
                # them) over instead of dropping them.
                tags.lyrics = lyrics
                tags.advisory = t.advisory
            if not tags.cover_bytes:
                # No canonical art available: keep whatever the file already
                # has rather than stripping it in the rewrite.
                data, ext = _read_embedded_picture(p)
                if data:
                    tags.cover_bytes = data
                    tags.cover_mime = "image/png" if ext == "png" else "image/jpeg"
            write_tags(p, tags)
            dest = build_destination(tags, p.suffix, library_root=lib_root)
            if dest != p:
                result = _safe_move(p, dest, overwrite=False)
                if result.moved:
                    _move_lyric_sidecar(p, dest)
                    source_dirs.add(p.parent)
                    moved_to = dest
                elif result.conflict and ctx:
                    ctx.log(f"fix-splits: destination conflict, tags fixed but not moved: {p} -> {dest}")
    except Exception:
        log.exception("fix-splits: rewrite failed for %s", p)
        return False
    dest_dirs.add(moved_to.parent)

    # Mirror the rewrite into the Track row (per-track commit: the file is
    # already mutated/moved on disk, a later cancel must not roll this back).
    db_t = s.get(Track, t.id)
    if db_t:
        db_t.path = str(moved_to)
        db_t.title = tags.title
        db_t.artist = tags.artist_display
        db_t.album = tags.album
        db_t.album_artist = tags.album_artist_display
        db_t.track_num = tags.track
        db_t.track_total = tags.track_total
        db_t.disc_num = tags.disc
        db_t.disc_total = tags.disc_total
        db_t.mb_track_id = tags.mb_track_id
        db_t.mb_album_id = tags.mb_album_id
        db_t.mb_release_group_id = tags.mb_release_group_id
        db_t.has_lyrics = bool(tags.lyrics)
        db_t.advisory = tags.advisory
        s.add(db_t)
    s.commit()
    if ctx and moved_to != p:
        ctx.log(f"moved {p.name}: {p.parent} -> {moved_to.parent}")
    return True


def fix_album_splits(folder_id: int, ctx=None) -> dict:
    """Repair albums whose tracks were identified against different releases.

    Independent per-file identification can scatter one album's tracks across
    several editions of the same MusicBrainz release group (different
    MUSICBRAINZ_ALBUMID / album title / track totals / covers), which players
    render as multiple albums. For each release-group whose tracks disagree,
    this elects a canonical release (the edition covering the most of the
    group's recordings, then Official status, then size) and fully re-tags
    every track against it — album-level fields, numbering, cover — while
    preserving embedded lyrics/advisory, then moves the files into the single
    canonical folder.

    Tracks whose recording genuinely isn't on the canonical release are left
    untouched (logged). Groups with no MusicBrainz ids fall back to the same
    offline majority-vote album/album_artist patch as
    ``check_album_consistency``. Protected tracks are always skipped.
    """
    from ..config import settings
    from ..tagging.coverart import fetch_for_release
    from .mover import write_cover_jpg
    from .organizer import _prune_empty_dirs

    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"ok": False, "reason": "Folder not found"}
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
        lib_root = Path(folder.path)

    groups = _build_album_groups(tracks)
    groups_fixed = 0
    tracks_retagged = 0
    tracks_voted = 0
    skipped_bonus = 0
    source_dirs: set[Path] = set()

    if ctx:
        ctx.progress(0, len(groups))
    with session() as s:
        for gi, group in enumerate(groups, start=1):
            if ctx:
                ctx.check_cancelled()
                ctx.progress(gi, len(groups), item=group[0].album or "")
            eligible = [t for t in group if not t.protected and Path(t.path).exists()]
            if len(eligible) < 2 or not _group_is_split(eligible):
                continue

            with_ids = [t for t in eligible if t.mb_album_id and t.mb_track_id]
            if not with_ids:
                # Offline fallback: no MB ids to re-resolve against, so unify
                # album/album_artist by majority vote like check_album_consistency.
                winning_album, winning_artist = _majority_pair(eligible)
                fixed = 0
                for t in eligible:
                    if t.album == winning_album and t.album_artist == winning_artist:
                        continue
                    if _normalize_track_to_pair(
                        s, t, winning_album, winning_artist, lib_root,
                        source_dirs, ctx, log_prefix="fix-splits",
                    ):
                        fixed += 1
                if fixed:
                    groups_fixed += 1
                    tracks_voted += fixed
                continue

            album_ids = {t.mb_album_id for t in with_ids}
            recording_ids = {t.mb_track_id for t in with_ids}
            canonical_id, release_doc, on_release = _elect_canonical_release(
                album_ids, recording_ids, ctx
            )
            if not canonical_id:
                if ctx:
                    ctx.log(f"fix-splits: no fetchable release for '{group[0].album}' — skipped")
                continue
            if ctx:
                ctx.log(
                    f"fix-splits: '{release_doc.get('title')}' -> canonical release "
                    f"{canonical_id} ({len(album_ids)} edition(s) in library)"
                )

            cover = None
            try:
                cover = fetch_for_release(canonical_id)
            except Exception as e:
                if ctx:
                    ctx.log(f"fix-splits: cover fetch failed for {canonical_id}: {e} — keeping embedded art")

            dest_dirs: set[Path] = set()
            fixed = 0
            for t in eligible:
                if ctx:
                    ctx.check_cancelled()
                if not t.mb_track_id or t.mb_track_id not in on_release:
                    # A recording genuinely absent from the canonical release
                    # (edition-exclusive track) — leave it as its own release
                    # rather than inventing a slot for it.
                    skipped_bonus += 1
                    if ctx:
                        ctx.log(f"fix-splits: '{t.title or Path(t.path).name}' is not on the canonical release — left as-is")
                    continue
                if _retag_to_canonical(
                    s, t, canonical_id, release_doc, cover, lib_root,
                    source_dirs, dest_dirs, ctx,
                ):
                    fixed += 1
            if fixed:
                groups_fixed += 1
                tracks_retagged += fixed
                if cover:
                    for d in dest_dirs:
                        write_cover_jpg(
                            d, cover.data,
                            min_overwrite_pixels=settings().cover_min_overwrite_pixels,
                            new_width=cover.width,
                        )
        s.commit()

    folders_merged = _prune_empty_dirs(source_dirs, lib_root) if source_dirs else 0
    summary = {
        "groups_fixed": groups_fixed,
        "tracks_retagged": tracks_retagged,
        "tracks_voted": tracks_voted,
        "skipped_bonus": skipped_bonus,
        "folders_merged": folders_merged,
    }
    log.info("fix_album_splits(%d): %s", folder_id, summary)
    if ctx:
        ctx.log(
            f"Unified {groups_fixed} split album(s): {tracks_retagged} track(s) re-tagged from MusicBrainz, "
            f"{tracks_voted} normalized offline, {skipped_bonus} edition-exclusive track(s) left as-is, "
            f"{folders_merged} empty folder(s) pruned"
        )
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
    if canonical is None:
        # Promote the widest candidate to cover.jpg.
        canonical = max(imgs, key=lambda p: (_image_width(p) or 0, p.name))
        target = d / "cover.jpg"
        if apply and canonical != target:
            try:
                with filelock.path_lock(canonical):
                    res = _safe_move(canonical, target, overwrite=False)
                if res.moved:
                    canonical = target
            except OSError:
                log.exception("cleanup: cover promote failed for %s", canonical)
        elif not apply:
            ctx and ctx.log(f"would promote {canonical.name} -> cover.jpg in {d}")
    removed = 0
    for img in imgs:
        if img.resolve() == canonical.resolve():
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
    """Move every file out of ``loser`` into ``target`` (audio preserving its
    relative sub-path, images flattened into the album root so the cover-dedupe
    can elect the widest, everything else quarantined). Track rows are repointed
    and committed per move. Returns per-loser counters."""
    from ..ingest.pipeline import SUPPORTED_EXTS

    counts = {"audio_moved": 0, "quarantined": 0, "skipped_protected": 0, "conflicts": 0}
    for dp, _dn, fns in os.walk(loser):
        for fn in fns:
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
                if not apply:
                    ctx and ctx.log(f"would relocate cover {src} -> {target}")
                    continue
                try:
                    cover_dest = unique_path(target / fn)
                    with filelock.path_lock(src):
                        _safe_move(src, cover_dest, overwrite=False)
                except OSError:
                    log.exception("cleanup: cover relocate failed for %s", src)
            else:
                if apply:
                    if _quarantine_file(src, lib_root, qroot, run_ts, ctx):
                        counts["quarantined"] += 1
                else:
                    ctx and ctx.log(f"would quarantine {src}")
                    counts["quarantined"] += 1
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
                    for k in ("audio_moved", "quarantined", "skipped_protected", "conflicts"):
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
                # Commit per rename: the file already moved on disk, so a
                # later cancel/exception must not roll this row back to a
                # path that no longer exists.
                s.commit()
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
    "fix_album_splits": (
        "Fix album splits",
        "Re-unify albums whose tracks were matched to different MusicBrainz editions "
        "(different album IDs, titles, track totals or covers — shown as several albums "
        "by players). Elects the release covering the most tracks and fully re-tags every "
        "track against it (network), preserving lyrics; merges files into one folder. "
        "Groups without MusicBrainz IDs get an offline album/album-artist majority vote. "
        "Skips protected tracks and edition-exclusive bonus tracks.",
        fix_album_splits),
    "unify_artist_folders": (
        "Fix artist folders",
        "Collapse duplicate artist folders that differ only by capitalization, "
        "punctuation/Unicode (curly quotes, dashes, ® marks) or MusicBrainz "
        "alias (FERG/A$AP Ferg) into one canonical folder per artist. Elects the "
        "majority album-artist spelling among your files, rewrites outlier "
        "album_artist tags to it and moves each file under the canonical folder "
        "(albums are left untouched), then renames case-only folder variants and "
        "prunes emptied folders. Offline; skips protected tracks. Run before "
        "album-folder consistency.",
        unify_artist_folders),
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
    "fix_disc_folders", "normalize_filenames", "unify_artist_folders",
    "check_album_consistency",
    "extract_covers", "prune", "cleanup", "find_duplicates", "find_missing_tracks",
]
BATCH_RETAG = ["validate_tags", "tag_advisories", "fix_genres", "replaygain"]

# Nuclear option: the full identify -> tag -> move pipeline runs first (added
# manually by the route layer, since it lives in ingest.bulk), then this list
# in order. Mirrors the logical dependency chain: tags/covers/lyrics/advisory
# data must exist before disc/filename cleanup, which must happen before
# ReplayGain (per-file loudness) and the report-only/cleanup passes.
BATCH_NUCLEAR = [
    # fix_album_splits runs right after the re-tag pipeline (prepended by the
    # route layer): per-file identification can still scatter one album over
    # several MB editions, and everything downstream (covers, disc folders,
    # consistency, ReplayGain's album gain) should see the unified state.
    "fix_album_splits",
    "validate_tags", "fetch_covers", "fetch_lyrics", "tag_advisories",
    "fix_disc_folders", "normalize_filenames", "unify_artist_folders",
    "check_album_consistency",
    "extract_covers", "replaygain", "find_duplicates", "find_missing_tracks", "prune",
    "cleanup",
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
