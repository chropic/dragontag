"""Read whatever tags already live on a file, normalized to a tiny dict.

The pipeline uses this as the first source of identification clues — if the
file came from Picard or another well-tagged source, we may already have a
``MUSICBRAINZ_TRACKID`` that lets us skip the search step entirely.

mutagen returns wildly different shapes depending on the file format (Vorbis
gives ``list[str]``, ID3 gives ``Frame`` objects with a ``.text`` list, MP4
gives raw atom values). The ``first()`` helper hides that mess.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import mutagen


def read(path: Path) -> dict[str, Any]:
    f = mutagen.File(str(path), easy=False)
    if f is None:
        # Unknown file type / not an audio file mutagen knows about.
        return {"duration": None}

    out: dict[str, Any] = {"duration": getattr(f.info, "length", None)}

    def first(*keys: str) -> str | None:
        """Return the first non-empty value found under any of ``keys``.

        Handles all three mutagen tag shapes:
        * vorbis-style list of strings
        * id3 Frame with ``.text``
        * mp4-style list (or, fallback, ``str``)
        """
        for k in keys:
            v = f.tags.get(k) if f.tags else None
            if not v:
                continue
            if hasattr(v, "text"):  # ID3 Frame
                return str(v.text[0]) if v.text else None
            if isinstance(v, list) and v:  # Vorbis / MP4
                return str(v[0])
            return str(v)
        return None

    # Each key is queried under several aliases so the same call works
    # against FLAC, MP3, MP4, WAV regardless of how they were tagged.
    out["title"] = first("TITLE", "title", "TIT2", "\xa9nam")
    out["artist"] = first("ARTIST", "artist", "TPE1", "\xa9ART")
    out["album"] = first("ALBUM", "album", "TALB", "\xa9alb")
    out["album_artist"] = first(
        "album_artist", "ALBUMARTIST", "albumartist", "TPE2", "aART"
    )
    out["mb_track_id"] = first("MUSICBRAINZ_TRACKID", "musicbrainz_trackid")
    out["mb_album_id"] = first("MUSICBRAINZ_ALBUMID", "musicbrainz_albumid")

    return out
