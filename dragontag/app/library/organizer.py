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
from pathlib import Path

from sqlmodel import select

from ..db import session
from ..library.mover import move
from ..library.paths import build_destination
from ..models import LibraryFolder, Track
from ..tagging.schema import TrackTags

log = logging.getLogger(__name__)


def organize_folder(folder_id: int) -> dict:
    """Move all tracks in folder_id to their canonical paths.

    Returns ``{"moved": N, "skipped": N, "errors": [...]}``.
    """
    moved = 0
    skipped = 0
    errors: list[str] = []

    with session() as s:
        folder = s.get(LibraryFolder, folder_id)
        if not folder:
            return {"moved": 0, "skipped": 0, "errors": ["Folder not found"]}
        lib_root = Path(folder.path)
        tracks = s.exec(
            select(Track).where(Track.library_folder_id == folder_id)
        ).all()

    for track in tracks:
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
            with session() as s2:
                t = s2.get(Track, track.id)
                if t:
                    t.path = str(dest)
                    s2.add(t)
                    s2.commit()
            moved += 1
            log.info("organize: %s -> %s", src.name, dest)
        except Exception as e:
            errors.append(f"error moving {src}: {e}")
            log.exception("organize: failed on %s", src)

    summary = {"moved": moved, "skipped": skipped, "errors": errors}
    log.info("organize folder %d complete: %s", folder_id, summary)
    return summary


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
