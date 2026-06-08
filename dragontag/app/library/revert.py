"""Undo a recorded tag write (see :class:`models.FileChange`).

A revert rewrites the file's original tags in place
(:func:`tagging.snapshot.restore`) and removes the ``cover.jpg`` sidecar
dragontag created. The file is **not** moved back to its pre-pipeline location —
doing so could land it back in the watched drop folder and trigger a re-ingest.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from ..db import session
from ..identify import existing_tags
from ..models import FileChange, Track
from ..tagging import snapshot


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
