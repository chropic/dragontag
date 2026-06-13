"""Undo a recorded tag write (see :class:`models.FileChange`).

A revert rewrites the file's original tags in place
(:func:`tagging.snapshot.restore`) and removes the ``cover.jpg`` sidecar
dragontag created. The file is **not** moved back to its pre-pipeline location —
doing so could land it back in the watched drop folder and trigger a re-ingest.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings, store
from ..db import session
from ..identify import existing_tags
from ..models import FileChange, Job, Track
from ..tagging import snapshot
from .mover import move
from .paths import unique_path

log = logging.getLogger(__name__)


def revert_change(change_id: int) -> tuple[bool, str]:
    """Revert a single FileChange. Returns ``(ok, message)`` for the UI toast."""
    with session() as s:
        change = s.get(FileChange, change_id)
        if change is None:
            return False, "Change not found."
        if change.reverted_at is not None:
            return False, "That change was already reverted."

        file = Path(change.file_path)
        if not file.exists():
            msg = f"File is no longer at {file.name}; cannot revert."
            change.revert_error = msg
            s.add(change)
            s.commit()
            return False, msg

        try:
            snapshot.restore(file, change.original_tags_json or {})
            if change.cover_jpg_created:
                cover = file.parent / "cover.jpg"
                if cover.exists():
                    cover.unlink()
            _refresh_track(s, file)
            _repair_job(s, change.job_id, file)
            change.reverted_at = datetime.utcnow()
            change.revert_error = None
            s.add(change)
            s.commit()
            return True, f"Reverted {file.name}."
        except Exception as e:  # noqa: BLE001 - surface any failure to the user
            change.revert_error = str(e)
            s.add(change)
            s.commit()
            return False, f"Revert failed: {e}"


def move_back(change_id: int) -> tuple[bool, str]:
    """Move a changed file back to its pre-pipeline directory.

    The restored path is added to the ``scan_exclude_files`` filter list so the
    watcher / scanner / bulk-retag don't immediately re-ingest it (the original
    location is often the watched drop folder).
    """
    with session() as s:
        change = s.get(FileChange, change_id)
        if change is None:
            return False, "Change not found."
        if not change.original_path:
            return False, "No original location was recorded for that change."
        file = Path(change.file_path)
        if not file.exists():
            return False, f"File is no longer at {file.name}; cannot move back."
        dest = Path(change.original_path)
        if dest == file:
            return False, "File is already at its original location."
        if dest.exists():
            dest = unique_path(dest)

        try:
            res = move(file, dest, overwrite=False)
            if not res.moved:
                return False, f"Could not move back: {dest} already exists."
        except Exception as e:  # noqa: BLE001 - surface any failure to the user
            return False, f"Move back failed: {e}"

        # The file has moved on disk. Persist the DB record of its new location
        # *before* touching the persistent exclude-list setting, and roll the
        # file back if the commit fails — otherwise a failed commit would leave
        # the file at ``dest`` while the DB (and the exclude list) disagree.
        track = s.exec(select(Track).where(Track.path == str(change.file_path))).first()
        if track:
            track.path = str(dest)
            s.add(track)
        _repair_job(s, change.job_id, dest)
        change.file_path = str(dest)
        s.add(change)
        try:
            s.commit()
        except Exception as e:  # noqa: BLE001
            log.exception("move-back: DB commit failed; restoring %s to %s", dest, file)
            try:
                move(dest, file, overwrite=False)
            except Exception:
                log.exception("move-back: rollback move failed for %s", dest)
            return False, f"Move back failed (DB); file restored to its previous location: {e}"

        # Only after the DB is durable do we exclude the restored path from
        # future automatic scans/ingests (the original location is often the
        # watched drop folder).
        excluded = list(settings().scan_exclude_files)
        if str(dest) not in excluded:
            excluded.append(str(dest))
            # FIFO cap so the list can't grow without bound.
            store().update({"scan_exclude_files": excluded[-500:]})

        return True, f"Moved {dest.name} back to {dest.parent}."


def _repair_job(s: Session, job_id: int | None, file: Path) -> None:
    """Point the originating Job at the file's current location so a requeue
    after a revert / move-back finds the file instead of erroring."""
    if not job_id:
        return
    job = s.get(Job, job_id)
    if job is None:
        return
    job.source_path = str(file)
    job.destination_path = str(file)
    s.add(job)


def _refresh_track(s: Session, file: Path) -> None:
    """Re-sync a Track row's denormalized tags after a revert (best-effort)."""
    track = s.exec(select(Track).where(Track.path == str(file))).first()
    if track is None:
        return
    info = existing_tags.read(file)
    track.title = info.get("title")
    track.artist = info.get("artist")
    track.album = info.get("album")
    track.album_artist = info.get("album_artist")
    track.mb_track_id = info.get("mb_track_id")
    track.mb_album_id = info.get("mb_album_id")
    s.add(track)
