"""Shared duplicate-track matching for reports and the ingest safety gate.

The library report and ingest pipeline must agree on what constitutes a likely
duplicate.  Keeping the comparison here prevents the safety gate from drifting
into a stricter or looser policy than the user-visible duplicate report.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from ..models import Track

DURATION_TOLERANCE_SECONDS = 3.0


def normalize_identity(value: str | None) -> str:
    """Normalize an artist/title identity without fuzzy punctuation folding."""
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def is_duplicate_track(
    track: Track,
    *,
    mb_track_id: str | None,
    artist: str | None,
    title: str | None,
    duration: float | None,
) -> bool:
    """Return whether ``track`` matches the resolved incoming recording."""
    if mb_track_id and track.mb_track_id and mb_track_id == track.mb_track_id:
        return True
    if not artist or not title or duration is None or track.duration is None:
        return False
    return (
        normalize_identity(track.artist) == normalize_identity(artist)
        and normalize_identity(track.title) == normalize_identity(title)
        and abs(float(track.duration) - float(duration)) <= DURATION_TOLERANCE_SECONDS
    )


def find_duplicate_tracks(
    tracks: Iterable[Track],
    *,
    mb_track_id: str | None,
    artist: str | None,
    title: str | None,
    duration: float | None,
    exclude_paths: Iterable[Path | str] = (),
) -> list[Track]:
    """Return matching tracks, excluding the file currently being re-tagged."""
    excluded = {_resolved_text(p) for p in exclude_paths}
    return [
        track
        for track in tracks
        if _resolved_text(track.path) not in excluded
        and is_duplicate_track(
            track,
            mb_track_id=mb_track_id,
            artist=artist,
            title=title,
            duration=duration,
        )
    ]


def _resolved_text(path: Path | str) -> str:
    try:
        resolved = str(Path(path).resolve(strict=False))
    except OSError:
        resolved = str(Path(path).absolute())
    return os.path.normcase(resolved)
