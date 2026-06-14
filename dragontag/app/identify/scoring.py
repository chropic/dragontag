"""Confidence scoring for an MB candidate against the source file's clues.

The score is a weighted sum of similarity signals. None of them are
authoritative on their own — title can collide on covers, artist on
prolific names, duration is exact for digital releases but skewed for
analog rips — so we lean on the combination.

Weights (tuned by feel against real-world test cases):

    title        0.35   # most reliable signal; mismatch usually means a cover
    artist       0.25
    album        0.15   # often missing; not penalized for being weak
    duration     0.15   # 0 sec diff -> 1.0, 5 sec -> 0.0, linear in between
    mb_search    0.10   # MB's own internal relevance score

The 0.85 default threshold in settings means a candidate basically has to
nail title + artist + (album OR duration) to auto-apply.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


def _norm(x: str) -> str:
    """Normalize for comparison: NFC-compose then casefold.

    NFC folds decomposed unicode (e.g. ``Cafe`` + combining acute) onto the
    composed form so a tag and an MB title that differ only in unicode form
    still match; casefold is the unicode-aware lowercase.
    """
    return unicodedata.normalize("NFC", x).casefold()


def _sim(a: str | None, b: str | None) -> float:
    """Case-insensitive, unicode-normalized string-similarity ratio in [0, 1]."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


@dataclass
class ScoreBreakdown:
    """Returned to the review UI so users can see *why* a score is low."""

    total: float
    title: float
    artist: float
    album: float
    duration: float
    mb_search_score: float


def score_candidate(
    *,
    candidate_recording: dict[str, Any],
    candidate_release: dict[str, Any],
    clues: dict[str, Any],
    mb_search_score: float = 0.0,
) -> ScoreBreakdown:
    """Score one candidate against the source-file clues. Pure function."""

    title_sim = _sim(candidate_recording.get("title"), clues.get("title"))

    # Pull the first artist name from the credit list — good enough for the
    # similarity check; we don't try to handle "Artist A & Artist B" style
    # joined credits specially because the title+album signals usually
    # disambiguate those.
    cand_artist = None
    credits = candidate_recording.get("artist-credit") or []
    if credits:
        if isinstance(credits[0], dict):
            cand_artist = credits[0].get("artist", {}).get("name") or credits[0].get("name")
        else:
            cand_artist = str(credits[0])
    artist_sim = _sim(cand_artist, clues.get("artist"))

    album_sim = _sim(candidate_release.get("title"), clues.get("album"))

    # Duration: MB stores ms; the pipeline gives us seconds. Convert,
    # take absolute delta, and scale so 0 sec → 1.0 and ≥5 sec → 0.0.
    duration = 0.0
    src_dur = clues.get("duration")
    cand_dur_ms = candidate_recording.get("length")
    # Explicit None checks: a 0-second value is a valid (if odd) duration and
    # must still participate, not be treated as "missing" by a truthiness test.
    if src_dur is not None and cand_dur_ms is not None:
        try:
            cand_sec = float(cand_dur_ms) / 1000.0
            delta = abs(cand_sec - float(src_dur))
            duration = max(0.0, 1.0 - delta / 5.0)
        except (TypeError, ValueError):
            duration = 0.0

    total = (
        0.35 * title_sim
        + 0.25 * artist_sim
        + 0.15 * album_sim
        + 0.15 * duration
        + 0.10 * max(0.0, min(1.0, mb_search_score))
    )
    return ScoreBreakdown(
        total=total,
        title=title_sim,
        artist=artist_sim,
        album=album_sim,
        duration=duration,
        mb_search_score=mb_search_score,
    )
