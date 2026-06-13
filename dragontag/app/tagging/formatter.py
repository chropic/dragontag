"""Optional string-formatting helpers for tag values.

Controlled by UserSettings.format_title_case, format_fix_qualifiers, and
format_grammar_correct. Applied in the pipeline after tag assembly and before
writing.
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

# Contraction map (case-insensitive lookup). The replacement preserves the
# casing pattern of the original word (all-upper, all-lower, capitalized).
#
# Only words whose un-apostrophed form is NOT itself a common English word are
# included. Ambiguous ones are deliberately omitted so we never corrupt valid
# titles: "were"→"we're" (past tense of be), "well"→"we'll", "wed"→"we'd"
# (to wed), "ill"→"I'll" (sick), "id"→"I'd" (the id) would all mangle
# legitimate lyrics like "We Were Young" or "All Is Well".
_CONTRACTIONS = {
    "dont": "don't", "cant": "can't", "wont": "won't",
    "isnt": "isn't", "wasnt": "wasn't", "arent": "aren't",
    "werent": "weren't", "hasnt": "hasn't", "havent": "haven't",
    "hadnt": "hadn't", "doesnt": "doesn't", "didnt": "didn't",
    "shouldnt": "shouldn't", "wouldnt": "wouldn't", "couldnt": "couldn't",
    "im": "I'm", "ive": "I've",
    "youre": "you're", "youll": "you'll", "youve": "you've", "youd": "you'd",
    "hes": "he's", "shes": "she's",
    "theyre": "they're", "theyll": "they'll", "theyve": "they've", "theyd": "they'd",
    "weve": "we've",
    "thats": "that's", "whats": "what's", "wheres": "where's",
    "whos": "who's", "hows": "how's", "lets": "let's",
}

# Bare nouns that, when found in ALL-CAPS/title context with a possessive S,
# should become possessives. Conservative — only common cases.
_POSSESSIVE_NOUNS = {
    "people", "men", "women", "children", "world", "everyone",
    "someone", "anyone", "nobody", "today", "yesterday", "tomorrow",
    "man", "woman", "child", "god", "lord",
}


def _match_case(replacement: str, source: str) -> str:
    """Mirror the casing pattern of ``source`` onto ``replacement``."""
    if source.isupper():
        return replacement.upper()
    if source.islower():
        return replacement.lower()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


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


def _apply_contractions(s: str) -> str:
    def repl(m: re.Match) -> str:
        word = m.group(0)
        key = word.lower()
        target = _CONTRACTIONS.get(key)
        if not target:
            return word
        # Preserve the source casing pattern (ALL CAPS, lower, Title).
        if word.isupper():
            return target.upper()
        if word.islower():
            return target.lower()
        if word[:1].isupper():
            return target[:1].upper() + target[1:]
        return target

    pattern = r"\b(" + "|".join(re.escape(k) for k in _CONTRACTIONS) + r")\b"
    return re.sub(pattern, repl, s, flags=re.IGNORECASE)


def _apply_possessives(s: str) -> str:
    """Convert known-noun + S into possessive when followed by another word."""
    def repl(m: re.Match) -> str:
        word = m.group(1)
        following = m.group(2)
        bare = word[:-1].lower()
        if bare not in _POSSESSIVE_NOUNS:
            return m.group(0)
        rep = word[:-1] + "'s"
        if word.isupper():
            rep = (word[:-1] + "'S").upper()
        elif word.islower():
            rep = word[:-1].lower() + "'s"
        elif word[:1].isupper():
            rep = word[:-1] + "'s"
        return rep + following

    # word ending in s, followed by space + non-space word (so not last)
    return re.sub(r"\b([A-Za-z]+s)(\s+\S+)", repl, s)


def _fix_punctuation_spacing(s: str) -> str:
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)        # no space before punctuation
    s = re.sub(r"([,.;:!?])([A-Za-z])", r"\1 \2", s)  # single space after
    s = re.sub(r"  +", " ", s)
    return s


def apply_grammar(
    s: str,
    *,
    fix_allcaps: bool = True,
    fix_contractions: bool = True,
    fix_possessives: bool = True,
    fix_punct_spacing: bool = True,
) -> str:
    """Run the grammar correction filter on ``s``.

    Each sub-rule is gated by an independent flag so callers (and the UI) can
    pick exactly which corrections to apply:

    * ``fix_allcaps`` — mostly-uppercase strings are lowercased and then
      title-cased so we don't shout downstream.
    * ``fix_contractions`` — DONT → don't, etc.
    * ``fix_possessives`` — PEOPLES X → people's X for a small allow-list.
    * ``fix_punct_spacing`` — normalize spacing around punctuation.
    """
    if not s:
        return s

    if fix_allcaps:
        letters = [c for c in s if c.isalpha()]
        if letters and len(s) > 3:
            upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            if upper_ratio > 0.6:
                s = to_title_case(s.lower())

    if fix_contractions:
        s = _apply_contractions(s)
    if fix_possessives:
        s = _apply_possessives(s)
    if fix_punct_spacing:
        s = _fix_punctuation_spacing(s)
    return s.strip()


def apply(
    s: str | None,
    *,
    title_case: bool = False,
    fix_quals: bool = False,
    grammar: bool = False,
    grammar_allcaps: bool = True,
    grammar_contractions: bool = True,
    grammar_possessives: bool = True,
    grammar_punct_spacing: bool = True,
) -> str | None:
    """Apply enabled formatters to ``s`` and return the result."""
    if not s:
        return s
    s = fix_grammar(s)
    if grammar:
        s = apply_grammar(
            s,
            fix_allcaps=grammar_allcaps,
            fix_contractions=grammar_contractions,
            fix_possessives=grammar_possessives,
            fix_punct_spacing=grammar_punct_spacing,
        )
    if title_case:
        s = to_title_case(s)
    if fix_quals:
        s = fix_qualifiers(s)
    return s
