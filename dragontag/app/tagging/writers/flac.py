"""FLAC / Vorbis Comments writer.

The canonical schema (see ``schema.py``) was designed for Vorbis, so this
writer is essentially a 1:1 mapping. The only complication is the embedded
front-cover image, which uses a separate FLAC PICTURE block rather than a
text tag.
"""
from __future__ import annotations

from pathlib import Path

from mutagen.flac import FLAC, Picture

from ..schema import TrackTags
from ._atomic import atomic_inplace
# _cap_cover lives in _id3common as the single canonical implementation; all
# writers share it so the 1200px cap and PNG/JPEG handling stay consistent.
from ._id3common import _cap_cover


def write(path: Path, tags: TrackTags, sep) -> None:
    with atomic_inplace(path) as tmp:
        audio = FLAC(str(tmp))
        # Wipe all existing Vorbis comments in memory. We *want* a clean
        # canonical state — otherwise old/non-conforming tags from another
        # tagger would coexist with ours, making downstream readers behave
        # unpredictably. ``tags.clear()`` does this without the extra on-disk
        # write that ``FLAC.delete()`` would perform (PICTURE blocks are
        # untouched and handled separately below via ``clear_pictures()``).
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()

        for k, v in tags.to_vorbis(sep).items():
            audio[k] = v

        # Embedded cover art: type=3 ("front cover") per the FLAC PICTURE spec.
        if tags.cover_bytes:
            audio.clear_pictures()  # avoid stacking covers across re-tags
            cover_data, cover_mime = _cap_cover(tags.cover_bytes, tags.cover_mime or "image/jpeg")
            pic = Picture()
            pic.type = 3
            pic.mime = cover_mime
            pic.desc = "Cover (front)"
            pic.data = cover_data
            audio.add_picture(pic)

        audio.save()
