"""Index existing on-disk files into the Track table.

Used to populate the library DB from files that were tagged outside of
dragontag (or by older versions) and to refresh metadata after manual edits.
Runs in a background daemon thread so the triggering HTTP request returns
immediately.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from ..db import session
from ..identify.existing_tags import read as read_existing
from ..ingest.pipeline import SUPPORTED_EXTS
from ..models import Track

log = logging.getLogger(__name__)

_BATCH_SIZE = 50


def scan_folder(folder_path: Path, folder_id: int) -> int:
    """Walk folder_path, upsert a Track row for every supported audio file.

    Returns the count of files processed.
    """
    count = 0
    batch: list[Path] = []
    for p in sorted(folder_path.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTS:
            continue
        batch.append(p)
        if len(batch) >= _BATCH_SIZE:
            _flush_batch(batch, folder_id)
            count += len(batch)
            batch = []
            if count % 500 == 0:
                log.info("scanner: %d files indexed in %s", count, folder_path)
    if batch:
        _flush_batch(batch, folder_id)
        count += len(batch)
    log.info("scanner: finished %s — %d files", folder_path, count)
    return count


def _flush_batch(paths: list[Path], folder_id: int) -> None:
    with session() as s:
        for p in paths:
            try:
                _upsert_from_disk(s, p, folder_id)
            except Exception:
                log.exception("scanner: failed to index %s", p)
                s.rollback()
        s.commit()


def _upsert_from_disk(s, path: Path, folder_id: int) -> Track:
    raw = read_existing(path)
    now = datetime.utcnow()
    fields = {
        "library_folder_id": folder_id,
        "title": raw.get("title"),
        "artist": raw.get("artist"),
        "album": raw.get("album"),
        "album_artist": raw.get("album_artist"),
        "track_num": _parse_num(raw.get("track")),
        "track_total": _parse_total(raw.get("track")),
        "disc_num": _parse_num(raw.get("disc")),
        "disc_total": _parse_total(raw.get("disc")) or _parse_num(raw.get("disc_total")),
        "duration": raw.get("duration"),
        "mb_track_id": raw.get("mb_track_id"),
        "mb_album_id": raw.get("mb_album_id"),
        "last_seen": now,
    }
    existing = s.exec(select(Track).where(Track.path == str(path))).first()
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        s.add(existing)
        return existing
    track = Track(path=str(path), indexed_at=now, **fields)
    s.add(track)
    return track


def _parse_num(v: str | None) -> int | None:
    """Parse "03" or "03/12" into 3."""
    if not v:
        return None
    try:
        return int(str(v).split("/")[0])
    except (ValueError, TypeError):
        return None


def _parse_total(v: str | None) -> int | None:
    """Parse "03/12" into 12; returns None for bare numbers."""
    if not v or "/" not in str(v):
        return None
    try:
        return int(str(v).split("/")[1])
    except (ValueError, TypeError, IndexError):
        return None
