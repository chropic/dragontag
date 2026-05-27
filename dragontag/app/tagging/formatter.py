"""Optional string-formatting helpers for tag values.

Controlled by UserSettings.format_title_case and format_fix_qualifiers.
Applied in the pipeline after tag assembly and before writing.
"""
from __future__ import annotations

import re

# Words that should stay lowercase in Title Case (articles, conjunctions, short prepositions).
_LOWERCASE_WORDS = {
    "a", "an", "the",
    "and", "but", "or", "nor", "for", "so", "yet",
    "at", "by", "in", "of", "on", "to", "up", "as", "it", "if",
    "via", "vs", "vs.", "feat", "feat.", "ft", "ft.",
}

# Qualifiers that should be wrapped in parentheses when found bare at end of title.
# Matched case-insensitively.
_QUALIFIERS = [
    "intro", "outro", "interlude",
    "live", "acoustic", "a cappella", "instrumental",
    "remix", "edit", "radio edit", "extended", "reprise",
    "version", "mix", "bonus", "demo",
]
_QUALIFIER_RE = re.compile(
    r"\s+[-–—]?\s*(" + "|".join(re.escape(q) for q in _QUALIFIERS) + r")\s*$",
    re.IGNORECASE,
)


def to_title_case(s: str) -> str:
    """Apply music-aware Title Case to ``s``.

    * First and last word are always capitalised.
    * Short articles/conjunctions/prepositions stay lowercase unless first/last.
    * Preserves existing all-caps acronyms (e.g. "DJ", "NYC", "II").
    """
    if not s:
        return s
    words = s.split(" ")
    result = []
    for i, word in enumerate(words):
        if not word:
            result.append(word)
            continue
        # Leave all-caps words (acronyms) intact.
        if word.isupper() and len(word) > 1:
            result.append(word)
            continue
        is_first = i == 0
        is_last = i == len(words) - 1
        lower = word.lower()
        if not is_first and not is_last and lower in _LOWERCASE_WORDS:
            result.append(lower)
        else:
            result.append(word[0].upper() + word[1:])
    return " ".join(result)


def fix_qualifiers(s: str) -> str:
    """Wrap bare trailing qualifiers in parentheses.

    ``"Song Name Live"`` → ``"Song Name (Live)"``
    ``"Track - Remix"``  → ``"Track (Remix)"``

    Already-parenthesised qualifiers are left alone.
    """
    if not s:
        return s

    def _replace(m: re.Match) -> str:
        q = m.group(1)
        # Capitalise first letter of qualifier.
        return f" ({q[0].upper()}{q[1:].lower()})"

    return _QUALIFIER_RE.sub(_replace, s)


def fix_grammar(s: str) -> str:
    """Fix common whitespace and punctuation issues."""
    if not s:
        return s
    s = re.sub(r"  +", " ", s)       # collapse multiple spaces
    s = s.strip()
    return s


def apply(s: str | None, *, title_case: bool = False, fix_quals: bool = False) -> str | None:
    """Apply enabled formatters to ``s`` and return the result."""
    if not s:
        return s
    s = fix_grammar(s)
    if title_case:
        s = to_title_case(s)
    if fix_quals:
        s = fix_qualifiers(s)
    return s
