"""Last-ditch filename parser.

Used when a file has no useful existing tags. Two common conventions:

* ``01 - Title.flac``           → strip leading number, return ``{title}``
* ``Artist - Title.flac``       → split on first `` - `` separator
* ``01 - Artist - Title.flac``  → strip number, then split

This is intentionally conservative — false-positive parses produce bad MB
search queries and dump the file in the review queue. If the heuristic isn't
confident, returning ``{title: stem}`` is fine: the MB search can still hit
on title alone.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Match a leading "01 - " / "01. " / "01 " etc.
_TRACK_PREFIX = re.compile(r"^\s*\d{1,3}\s*[-.\s]+\s*")


def parse(path: Path) -> dict[str, str | None]:
    # NFC-normalize so decomposed unicode in filenames composes the same way MB
    # data does, keeping later string comparisons consistent.
    stem = unicodedata.normalize("NFC", path.stem)
    stripped = _TRACK_PREFIX.sub("", stem)
    if " - " in stripped:
        artist, _, title = stripped.partition(" - ")
        return {"artist": artist.strip() or None, "title": title.strip() or None}
    return {"artist": None, "title": stripped.strip() or None}
