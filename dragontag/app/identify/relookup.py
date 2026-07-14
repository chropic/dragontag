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
    text_fallback: bool = True,
) -> tuple[list, bool]:
    """Return ``(candidates, fingerprinted)``.

    ``fingerprinted`` is True only when the list came from an AcoustID recording
    match (high confidence); a text-search fallback returns False so callers can
    refuse to auto-apply a fuzzy guess. Network errors are swallowed to an empty
    list (``acoustid.lookup`` and ``search_candidates`` both degrade to ``[]``),
    matching the interactive Identify route.

    ``text_fallback=False`` skips the MusicBrainz text search entirely when the
    fingerprint yields nothing — used by the batch re-identify, which only ever
    applies fingerprint-confirmed matches and would otherwise burn a rate-limited
    MB search per unmatched file for a result it discards.
    """
    matches = acoustid.lookup(path)
    if matches and matches[0].recording_id:
        cands = mbq.candidates_from_mbid(matches[0].recording_id)
        if cands:
            return cands, True
    if not text_fallback:
        return [], False
    cands = mbq.search_candidates(title=title, artist=artist, album=album, limit=limit)
    return cands, False
