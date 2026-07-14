"""Shared per-file re-identification lookup.

Given a file on disk plus its current text tags, return MusicBrainz candidates
via an AcoustID fingerprint (high confidence) or a plain text-search fallback.
Used by the per-track "Identify" route and the batch "Re-identify untagged
tracks" action so both agree on how a lone file is looked up. Pure network — no
database access and no file locks.
"""
from __future__ import annotations

from pathlib import Path

from . import acoustid
from . import musicbrainz as mbq


def candidates_for_file(
    path: Path,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    limit: int = 10,
) -> tuple[list, bool]:
    """Return ``(candidates, fingerprinted)``.

    ``fingerprinted`` is True only when the list came from an AcoustID recording
    match (high confidence); a text-search fallback returns False so callers can
    refuse to auto-apply a fuzzy guess. Network errors are swallowed to an empty
    list (``acoustid.lookup`` and ``search_candidates`` both degrade to ``[]``),
    matching the interactive Identify route.
    """
    matches = acoustid.lookup(path)
    if matches and matches[0].recording_id:
        cands = mbq.candidates_from_mbid(matches[0].recording_id)
        if cands:
            return cands, True
    cands = mbq.search_candidates(title=title, artist=artist, album=album, limit=limit)
    return cands, False
