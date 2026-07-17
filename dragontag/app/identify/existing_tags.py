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


def _coerce(v: Any) -> str | None:
    """Normalize one raw mutagen tag value (any format) to a single string.

    Handles all three mutagen tag shapes:
    * id3 Frame with ``.text``
    * vorbis-style / mp4-style ``list``
    * a bare ``str`` fallback

    MP4 ``trkn``/``disk`` come back as a ``(number, total)`` tuple; it's
    rendered as ``"N/T"`` so the scanner's ``_parse_num``/``_parse_total``
    (which split on ``/``) read both halves instead of choking on the tuple's
    repr (``"(5, 12)"``).
    """
    if hasattr(v, "text"):  # ID3 Frame
        return str(v.text[0]) if v.text else None
    if hasattr(v, "data"):  # ID3 UFID frame — payload is raw bytes, no .text
        raw = v.data
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    if isinstance(v, list) and v:  # Vorbis / MP4
        item = v[0]
        if isinstance(item, tuple):
            return "/".join(str(x) for x in item)
        if isinstance(item, bytes):  # MP4 freeform atoms are bytes (MP4FreeForm)
            return item.decode("utf-8", "replace")
        return str(item)
    return str(v)


def read(path: Path) -> dict[str, Any]:
    try:
        f = mutagen.File(str(path), easy=False)
    except Exception:
        # Truncated / corrupt-but-known file: mutagen raises (HeaderNotFoundError,
        # MutagenError, …). Degrade to "no clues" so the pipeline falls back to
        # the filename + MB search and routes to review, instead of erroring the
        # whole job on an unreadable header.
        return {"duration": None}
    if f is None:
        # Unknown file type / not an audio file mutagen knows about.
        return {"duration": None}

    out: dict[str, Any] = {"duration": getattr(f.info, "length", None)}

    def first(*keys: str) -> str | None:
        """Return the first non-empty value found under any of ``keys``."""
        for k in keys:
            # Some tag containers validate the key on lookup instead of just
            # missing it: mutagen's Vorbis ``VCommentDict.get`` routes through
            # ``__getitem__`` and raises ``ValueError`` for a non-ASCII key (the
            # MP4-style ``\xa9nam``/``\xa9ART`` aliases below), so ``.get`` does
            # NOT honour the usual "missing → None" contract here. Treat any
            # raising key as "not present" and move on. (``_has_lyrics`` guards
            # the same way for the same reason.)
            try:
                v = f.tags.get(k) if f.tags else None
            except Exception:
                v = None
            if not v:
                continue
            return _coerce(v)
        return None

    # Each key is queried under several aliases so the same call works
    # against FLAC, MP3, MP4, WAV regardless of how they were tagged.
    out["title"] = first("TITLE", "title", "TIT2", "\xa9nam")
    out["artist"] = first("ARTIST", "artist", "TPE1", "\xa9ART")
    out["album"] = first("ALBUM", "album", "TALB", "\xa9alb")
    out["album_artist"] = first(
        "album_artist", "ALBUMARTIST", "albumartist", "TPE2", "aART"
    )
    # MB ids live under format-specific keys: bare Vorbis names (FLAC), our
    # writers' TXXX:/----: prefixed names plus the UFID recording-id frame
    # (MP3/WAV/MP4), and Picard's space-separated descriptions. Without the
    # prefixed aliases the MBID short-circuit only ever fires for FLAC.
    out["mb_track_id"] = first(
        "MUSICBRAINZ_TRACKID", "musicbrainz_trackid",
        "TXXX:MUSICBRAINZ_TRACKID", "TXXX:MusicBrainz Track Id",
        "UFID:http://musicbrainz.org",
        "----:com.apple.iTunes:MUSICBRAINZ_TRACKID",
        "----:com.apple.iTunes:MusicBrainz Track Id",
    )
    out["mb_album_id"] = first(
        "MUSICBRAINZ_ALBUMID", "musicbrainz_albumid",
        "TXXX:MUSICBRAINZ_ALBUMID", "TXXX:MusicBrainz Album Id",
        "----:com.apple.iTunes:MUSICBRAINZ_ALBUMID",
        "----:com.apple.iTunes:MusicBrainz Album Id",
    )
    out["mb_release_group_id"] = first(
        "MUSICBRAINZ_RELEASEGROUPID", "musicbrainz_releasegroupid",
        "TXXX:MUSICBRAINZ_RELEASEGROUPID", "TXXX:MusicBrainz Release Group Id",
        "----:com.apple.iTunes:MUSICBRAINZ_RELEASEGROUPID",
        "----:com.apple.iTunes:MusicBrainz Release Group Id",
    )
    out["mb_album_artist_id"] = first(
        "MUSICBRAINZ_ALBUMARTISTID", "musicbrainz_albumartistid",
        "TXXX:MUSICBRAINZ_ALBUMARTISTID", "TXXX:MusicBrainz Album Artist Id",
        "----:com.apple.iTunes:MUSICBRAINZ_ALBUMARTISTID",
        "----:com.apple.iTunes:MusicBrainz Album Artist Id",
    )
    out["track"] = first("TRACKNUMBER", "tracknumber", "track", "TRCK", "trkn")
    out["disc"] = first("DISCNUMBER", "discnumber", "disc", "TPOS", "disk")
    out["disc_total"] = first("DISCTOTAL", "TOTALDISCS", "totaldiscs", "disctotal")

    # ----- explicit advisory + lyrics presence (drives dashboard counters) -----
    # ITUNESADVISORY (Vorbis), TXXX:ITUNESADVISORY (ID3), rtng (MP4). Normalize
    # to dragontag's convention: 1 = explicit, 0 = clean, None = unknown.
    out["advisory"] = _norm_advisory(
        first("ITUNESADVISORY", "itunesadvisory", "TXXX:ITUNESADVISORY", "rtng")
    )
    out["has_lyrics"] = _has_lyrics(f.tags)

    return out


def _norm_advisory(raw: str | None) -> int | None:
    """Normalize an advisory tag value to 1 (explicit), 0 (clean) or None.

    dragontag writes ``0`` for clean and ``1`` for explicit; iTunes-tagged
    files use ``2`` for clean and ``1`` for explicit (``4`` is the legacy
    iTunes explicit code), so all of those map onto 0/1. Anything else (e.g.
    an empty or unrecognized rating) is treated as unknown.
    """
    if raw is None:
        return None
    try:
        v = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    if v in (1, 4):
        return 1
    if v in (0, 2):
        return 0
    return None


def _has_lyrics(tags: Any) -> bool:
    """True when the file carries any embedded lyrics tag.

    Covers ID3 ``USLT`` frames (keyed ``USLT::lang``, so a plain ``.get`` misses
    them — use ``getall``), Vorbis ``LYRICS``/``UNSYNCEDLYRICS``, and MP4 ``\xa9lyr``.
    """
    if tags is None:
        return False
    getall = getattr(tags, "getall", None)
    if callable(getall):
        try:
            if getall("USLT"):
                return True
        except Exception:
            pass
    for k in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics", "\xa9lyr"):
        try:
            v = tags.get(k)
        except Exception:
            v = None
        if v:
            return True
    return False
