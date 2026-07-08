"""MP3 writer — thin wrapper around the shared ID3v2.4 frame builder.

We always force ID3v2.4 (``v2_version=4``) on save because v2.3 doesn't
support multi-value text frames and rounds dates to YYYY.
"""
from __future__ import annotations

from pathlib import Path

from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from ..schema import TrackTags
from ._atomic import atomic_inplace
from ._id3common import populate_id3


def write(path: Path, tags: TrackTags, sep) -> None:
    # Mutate a temp copy and atomically swap it in so a crash mid-save can
    # never corrupt the original audio file (see ``_atomic.atomic_inplace``).
    with atomic_inplace(path) as tmp:
        # MP3() swallows a missing ID3 header, leaving ``tags`` as None.
        audio = MP3(str(tmp), ID3=ID3)
        if audio.tags is None:
            audio.add_tags()

        populate_id3(audio.tags, tags, sep)
        audio.save(v2_version=4)
