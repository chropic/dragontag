"""Split a multi-artist credit string into individual artist names.

Used both when assembling tags from a fresh MusicBrainz credit (an MB
artist-credit *item* can itself carry an unsplit "X feat. Y" or "X, Y" name
with no joinphrase boundary — MB doesn't always model every collaboration as
separate credit entries) and when writing a literal artist/album_artist
string with no MB reassembly (``tagging/partial.write_basic_tags``).

Heuristic (case-insensitive throughout):
* "feat."/"ft."/"featuring" (word-boundary) and "&" always split.
* "," splits UNLESS the text immediately following it starts with "the "
  (so "Tyler, The Creator" stays intact, but "Diplo, SIDEPIECE" splits).
  Each comma is checked independently so "A, The Roots, B" splits on the
  second comma but not the first.
* Returns ``[name]`` unchanged if no separator is found — never breaks a
  single-artist name.
"""
from __future__ import annotations

import re

# feat./ft./featuring: same word-boundary convention as paths._FEAT_RE, but
# here it splits into two pieces (a primary + a featured artist) instead of
# truncating the featured part away.
_FEAT_SPLIT_RE = re.compile(r"\s*[\s(\[]*\b(?:feat\.?|ft\.?|featuring)\b\.?\s*", re.IGNORECASE)
_AMP_RE = re.compile(r"\s*&\s*")
# Comma followed by optional whitespace then "the " (case-insensitive) is
# protected; any other comma is a split point.
_COMMA_SPLIT_RE = re.compile(r",(?!\s*the\s)", re.IGNORECASE)


def split_multi_artist(name: str | None) -> list[str]:
    """Split ``name`` into individual artist names. Never raises."""
    if not name or not name.strip():
        return [name] if name else []
    pieces = _FEAT_SPLIT_RE.split(name)
    pieces = [p for piece in pieces for p in _AMP_RE.split(piece)]
    pieces = [p for piece in pieces for p in _COMMA_SPLIT_RE.split(piece)]
    out = [p.strip() for p in pieces]
    out = [p for p in out if p]
    return out or [name.strip()]
