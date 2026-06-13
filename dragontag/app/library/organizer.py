"""Re-organize existing library files to match the current filename/folder templates.

For each Track in a LibraryFolder, compute where the file *should* be based on
its stored tag snapshot and the active settings, then move it if the path has
changed.  The Track row is updated to reflect the new path.

Design notes:
* Tags are read from the Track DB row, not re-read from disk.  If the user
  has edited tags outside dragontag they should run a library scan first.
* Conflicts (destination already occupied by a different file) are logged and
  counted but do not abort the run; the user can re-run after resolving them.
* Runs in a background daemon thread so the triggering HTTP request returns
  immediately.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlmodel import select

from ..db import session
from ..library.mover import move
from ..library.paths import build_destination
from ..models import LibraryFolder, Track
from ..tagging.schema import TrackTags

log = logging.getLogger(__name__)


def organize_folder(folder_id: int, ctx=None) -> dict:
    """Move all tracks in folder_id to their canonical paths.

    ``ctx`` is an optional ``tasks.TaskCtx`` for progress reporting.
    Returns ``{"moved": N, "skipped": N, "errors": [...]}``.
    """
    moved = 0
    skipped = 0
    errors: list[str] = []
    source_dirs: set[Path] = set()

    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"moved": 0, "skipped": 0, "errors": ["Folder not found"]}
        lib_root = Path(folder.path)
        tracks = s.exec(
            select(Track).where(Track.library_folder_id == folder_id)
        ).all()

    if ctx:
        ctx.progress(0, len(tracks))
    for i, track in enumerate(tracks, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(tracks), item=Path(track.path).name)
        source_dirs.add(Path(track.path).parent)
        src = Path(track.path)
        if not src.exists():
            errors.append(f"missing: {src}")
            continue
        try:
            tags = _track_to_tags(track)
            dest = build_destination(tags, src.suffix, library_root=lib_root)
            if dest == src:
                skipped += 1
                continue
            result = move(src, dest, overwrite=False)
            if result.conflict:
                errors.append(f"conflict: {src} -> {dest}")
                continue
            # The file has already moved on disk. ``Track.path`` is the only
            # record of where it now lives, so if the DB update fails we must
            # move the file back rather than leave the library pointing at a
            # path that no longer holds the file.
            try:
                with session() as s2:
                    t = s2.get(Track, track.id)
                    if t:
                        t.path = str(dest)
                        s2.add(t)
                        s2.commit()
            except Exception:
                log.exception("organize: DB update failed; rolling %s back to %s", dest, src)
                try:
                    move(dest, src, overwrite=False)
                    errors.append(f"db-failed (rolled back): {src}")
                except Exception:
                    log.exception("organize: rollback move failed for %s", dest)
                    errors.append(f"DIVERGED: file at {dest} but DB has {src}")
                continue
            moved += 1
            log.info("organize: %s -> %s", src.name, dest)
        except Exception as e:
            errors.append(f"error moving {src}: {e}")
            log.exception("organize: failed on %s", src)

    removed_dirs = _prune_empty_dirs(source_dirs, lib_root)

    summary = {
        "moved": moved,
        "skipped": skipped,
        "errors": errors,
        "removed_dirs": removed_dirs,
    }
    log.info("organize folder %d complete: %s", folder_id, summary)
    if ctx:
        ctx.log(f"Moved {moved}, skipped {skipped}, errors {len(errors)}, pruned {removed_dirs} empty dir(s)")
    return summary


def _prune_empty_dirs(starting_dirs: set[Path], lib_root: Path) -> int:
    """Remove directories under ``lib_root`` that are *completely* empty.

    Never deletes a directory that contains any file. Walks bottom-up so that
    parents become eligible after their (empty) children are removed. Stops at
    ``lib_root`` itself — never deletes the library root.
    """
    removed = 0
    try:
        lib_root_resolved = lib_root.resolve()
    except Exception:
        return 0

    # Build the set of all candidate dirs: every original source dir and every
    # ancestor up to (but not including) the library root.
    candidates: set[Path] = set()
    for d in starting_dirs:
        try:
            dr = d.resolve()
        except Exception:
            continue
        try:
            dr.relative_to(lib_root_resolved)
        except ValueError:
            continue  # outside the library, skip
        cur = dr
        while cur != lib_root_resolved and cur.parent != cur:
            candidates.add(cur)
            cur = cur.parent

    # Sort by depth descending so children are processed before parents.
    for d in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
        try:
            if not d.exists() or not d.is_dir():
                continue
            if any(d.iterdir()):
                continue  # contains *anything* (file or non-empty dir) — skip
            os.rmdir(d)
            removed += 1
            log.info("organize: removed empty dir %s", d)
        except OSError as e:
            log.debug("organize: could not remove %s: %s", d, e)
    return removed


def _track_to_tags(track: Track) -> TrackTags:
    """Build the minimal TrackTags subset needed by build_destination."""
    return TrackTags(
        title=track.title,
        artist_display=track.artist,
        album=track.album,
        album_artist_display=track.album_artist,
        track=track.track_num,
        disc=track.disc_num,
        disc_total=track.disc_total,
    )
