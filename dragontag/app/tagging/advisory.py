"""Explicit-content classifier.

Uses word-boundary regex matching against a predefined list of explicit words.
Logic ported from L:\\Files\\Repos\\autoadvisory\\app\\advisory.py.
"""
from __future__ import annotations

import re

EXPLICIT_WORDS = [
    r"fuck", r"fucker", r"fuckin", r"fucking", r"fucked", r"fucks",
    r"motherfuck", r"motherfucker", r"motherfucking", r"motherfuckers",
    r"shit", r"shitty", r"shitting", r"bullshit",
    r"bitch", r"bitches", r"bitching", r"bitchass",
    r"nigga", r"niggas", r"nigger", r"niggers",
    r"ass(?:hole|holes|wipe)",
    r"cock(?:sucker|sucking)?",
    r"cunt", r"cunts",
    r"dick(?:head|s)?",
    r"pussy", r"pussies",
    r"whore", r"whores",
    r"slut", r"sluts",
    r"damn", r"goddamn",
    r"piss", r"pissed", r"pissing",
]

_PATTERN = re.compile(
    r"\b(?:" + "|".join(EXPLICIT_WORDS) + r")\b",
    re.IGNORECASE,
)

_LRC_TS = re.compile(r"\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]\s*")


def strip_lrc_timestamps(text: str) -> str:
    return _LRC_TS.sub("", text)


def is_explicit(lyrics: str) -> bool:
    return bool(_PATTERN.search(strip_lrc_timestamps(lyrics)))
