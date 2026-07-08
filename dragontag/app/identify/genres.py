"""Genre filtering for MusicBrainz community tags.

MB "genres" are free-form community tags, so junk like "billboard top 100",
"seen live" or "my favourite albums" routinely outranks real genres. We filter
candidates against a vendored canonical whitelist (``data/genres.txt``); when
*nothing* survives the whitelist we fall back to the raw tags minus an
explicit junk blacklist, so legitimately obscure genres aren't lost.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "genres.txt"

# Patterns that are clearly not genres, used only in the no-whitelist-match
# fallback. Kept short and unambiguous on purpose.
_JUNK_RES = [
    re.compile(p)
    for p in (
        r"\bbillboard\b",
        r"\btop \d+\b",
        r"\bcharts?\b",
        r"\bseen live\b",
        r"\bfavou?rites?\b",
        r"\bown(ed)?\b",
        r"\bcheck out\b",
        r"\bfixme\b",
        r"\b\d+ of \d+\b",
        r"\b(19|20)\d{2}\b",          # bare years / "best of 2011"
        r"\bspotify\b",
        r"\bplaylist\b",
        r"\bawesome\b",
        r"\blove(d)? it\b",
        r"\bto listen\b",
    )
]


@lru_cache(maxsize=1)
def load_whitelist() -> frozenset[str]:
    """Read the vendored genre list (lowercase, one per line, # comments)."""
    out: set[str] = set()
    try:
        for line in _DATA_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                out.add(line)
    except OSError:
        pass
    return frozenset(out)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _is_junk(name: str) -> bool:
    return any(rx.search(name) for rx in _JUNK_RES)


def filter_genres(raw: list[str]) -> list[str]:
    """Return ``raw`` reduced to plausible genres, original order preserved.

    Whitelist matching is hyphen/space-insensitive ("hip hop" == "hip-hop").
    If no candidate is whitelisted, raw tags that don't match the junk
    blacklist are kept instead — an empty result only happens when every tag
    looked like junk.
    """
    wl = load_whitelist()
    matched: list[str] = []
    seen: set[str] = set()
    for name in raw:
        n = _norm(name)
        # Dedup on the hyphen-collapsed key: matching is hyphen/space-agnostic,
        # so "Hip Hop" and "Hip-Hop" are the same genre and must not both survive.
        dk = n.replace("-", " ")
        if not n or dk in seen:
            continue
        if n in wl or n.replace("-", " ") in wl or n.replace(" ", "-") in wl:
            seen.add(dk)
            matched.append(name)
    if matched:
        return matched
    # Fallback: nothing whitelisted — keep non-junk raw tags.
    out: list[str] = []
    seen = set()
    for name in raw:
        n = _norm(name)
        dk = n.replace("-", " ")
        if n and dk not in seen and not _is_junk(n):
            seen.add(dk)
            out.append(name)
    return out
