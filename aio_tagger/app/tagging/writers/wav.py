"""WAV writer.

RIFF WAV files can hold an ID3 chunk; mutagen's ``WAVE`` class exposes it
through the same ``ID3``-frame API as MP3. We delegate to the shared frame
builder so MP3 and WAV produce identical tag sets.
"""
from __future__ import annotations

from pathlib import Path

from mutagen.wave import WAVE

from ..schema import TrackTags
from ._id3common import populate_id3


def write(path: Path, tags: TrackTags, sep) -> None:
    audio = WAVE(str(path))
    if audio.tags is None:
        audio.add_tags()
    populate_id3(audio.tags, tags, sep)
    audio.save()
