"""FLAC / Vorbis Comments writer.

The canonical schema (see ``schema.py``) was designed for Vorbis, so this
writer is essentially a 1:1 mapping. The only complication is the embedded
front-cover image, which uses a separate FLAC PICTURE block rather than a
text tag.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from mutagen.flac import FLAC, Picture

from ..schema import TrackTags

_MAX_COVER_PX = 1200


def _cap_cover(data: bytes, mime: str) -> tuple[bytes, str]:
    """Resize cover art to at most _MAX_COVER_PX on the longest side.

    Returns the original bytes and mime unchanged when the image is small
    enough or can't be decoded — re-encoding always reports the mime that
    matches the bytes actually produced, never the original declared one.
    """
    from PIL import Image
    try:
        img = Image.open(BytesIO(data))
        if max(img.size) <= _MAX_COVER_PX:
            return data, mime
        img.thumbnail((_MAX_COVER_PX, _MAX_COVER_PX), Image.LANCZOS)
        out = BytesIO()
        if "png" in mime:
            fmt, out_mime = "PNG", "image/png"
        else:
            fmt, out_mime = "JPEG", "image/jpeg"
        img.convert("RGB").save(out, format=fmt, quality=85)
        return out.getvalue(), out_mime
    except Exception:
        return data, mime


def write(path: Path, tags: TrackTags, sep) -> None:
    audio = FLAC(str(path))
    # Wipe all existing Vorbis comments. We *want* a clean canonical state —
    # otherwise old/non-conforming tags from another tagger would coexist
    # with our new ones, which makes downstream readers behave unpredictably.
    audio.delete()

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
