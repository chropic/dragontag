"""Shared ID3v2.4 frame builder used by both the MP3 and WAV writers.

The two file formats use the exact same ID3 frames — the only difference is
the container (MPEG audio vs. RIFF WAV). Centralizing the frame-construction
logic here keeps the format-specific writers tiny.

Mapping strategy:
* Standard ID3 frames cover the well-known fields (title, artist, album, etc.).
* Anything outside that set (MusicBrainz IDs, RELEASETYPE, BARCODE, ARTISTS
  multi-value, sort names, …) goes into ``TXXX:<NAME>`` frames. MusicBrainz
  Picard uses the same convention, which means our output is interoperable
  with anything that reads Picard-tagged files.
* The MB recording (track) MBID also gets a ``UFID:http://musicbrainz.org``
  frame because Picard writes one, and some readers look there first.
"""
from __future__ import annotations

from io import BytesIO

from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCMP,
    TCOM,
    TCON,
    TDRC,
    TIT2,
    TLAN,
    TMED,
    TEXT,
    TPE1,
    TPE2,
    TPE3,
    TPOS,
    TPUB,
    TRCK,
    TSO2,
    TSOP,
    TSRC,
    TXXX,
    UFID,
    USLT,
)

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
        if "png" in mime.lower():
            fmt, out_mime = "PNG", "image/png"
        else:
            fmt, out_mime = "JPEG", "image/jpeg"
        img.convert("RGB").save(out, format=fmt, quality=85)
        return out.getvalue(), out_mime
    except Exception:
        return data, mime


# Anything from the canonical Vorbis schema that doesn't have a dedicated
# ID3 frame goes through a TXXX:<NAME> frame. The names are kept identical
# to the Vorbis keys for cross-format consistency. Sort names are deliberately
# absent: they have dedicated TSOP (ARTISTSORT) / TSO2 (ALBUMARTISTSORT) frames
# written below, so listing them here too would emit redundant TXXX copies.
TXXX_FIELDS = (
    "ARTISTS",
    "RELEASECOUNTRY",
    "RELEASESTATUS",
    "RELEASETYPE",
    "SCRIPT",
    "BARCODE",
    "ORIGINALDATE",
    "ORIGINALYEAR",
    "ACOUSTID_ID",
    "MUSICBRAINZ_RELEASETRACKID",
    "MUSICBRAINZ_ALBUMID",
    "MUSICBRAINZ_ALBUMARTISTID",
    "MUSICBRAINZ_ARTISTID",
    "MUSICBRAINZ_RELEASEGROUPID",
    "TRACKTOTAL",
    "TOTALTRACKS",
    "DISCTOTAL",
    "TOTALDISCS",
    "CATALOGNUMBER",
    "ARRANGER",
    "TAGGER",
)


def populate_id3(id3: ID3, tags: TrackTags, sep) -> None:
    """Replace the entire ID3 frame set on ``id3`` with the data in ``tags``."""

    # Same reasoning as the FLAC writer: blow away any pre-existing frames so
    # we end with exactly the canonical set rather than a merge. ``clear()``
    # drops all frames in-memory (the file is persisted later by the caller's
    # ``save()``); ``delete()`` would do immediate file I/O and, on WAV's
    # ``_WaveID3``, requires a positional ``filething`` it isn't given here.
    id3.clear()
    v = tags.to_vorbis(sep)

    def add_frame(frame_cls, key: str) -> None:
        # ``v[key]`` may be a str (single-value field) or a list[str]
        # (multi-value field) — ID3v2.4 text frames accept either and write
        # multiple values natively for the list form.
        if key in v:
            id3.add(frame_cls(encoding=3, text=v[key]))

    # ----- standard frames -----
    add_frame(TIT2, "TITLE")
    add_frame(TPE1, "ARTIST")
    add_frame(TPE2, "album_artist")  # ID3 album-artist frame
    add_frame(TALB, "ALBUM")
    add_frame(TCOM, "COMPOSER")
    add_frame(TCON, "GENRE")
    add_frame(TPUB, "LABEL")
    add_frame(TMED, "MEDIA")
    add_frame(TDRC, "DATE")
    add_frame(TSOP, "ARTISTSORT")
    add_frame(TSO2, "ALBUMARTISTSORT")
    add_frame(TLAN, "LANGUAGE")
    # TPE3 = conductor, TEXT = lyricist (standard ID3v2.4 frames). Pass the
    # lists directly so each value is a native multi-value entry.
    if tags.conductor:
        id3.add(TPE3(encoding=3, text=tags.conductor))
    if tags.lyricist:
        id3.add(TEXT(encoding=3, text=tags.lyricist))
    if tags.compilation:
        id3.add(TCMP(encoding=3, text="1"))

    # TRCK/TPOS accept the "NN/TT" form directly — we just feed in what
    # ``to_vorbis`` already produced.
    if "track" in v:
        id3.add(TRCK(encoding=3, text=v["track"]))
    if "disc" in v:
        id3.add(TPOS(encoding=3, text=v["disc"]))

    # TSRC is multi-value in ID3v2.4 — pass the list directly.
    if tags.isrcs:
        id3.add(TSRC(encoding=3, text=tags.isrcs))

    # UFID for the MB recording id — owner string is the conventional URL,
    # matching Picard's behavior.
    if tags.mb_track_id:
        id3.add(
            UFID(owner="http://musicbrainz.org", data=tags.mb_track_id.encode("utf-8"))
        )

    # ----- non-standard / MusicBrainz-flavored fields via TXXX -----
    for k in TXXX_FIELDS:
        if k in v:
            id3.add(TXXX(encoding=3, desc=k, text=v[k]))

    # ----- embedded front cover -----
    if tags.cover_bytes:
        id3.delall("APIC")
        cover_data, cover_mime = _cap_cover(tags.cover_bytes, tags.cover_mime or "image/jpeg")
        id3.add(
            APIC(
                encoding=3,
                mime=cover_mime,
                type=3,  # front cover
                desc="Cover (front)",
                data=cover_data,
            )
        )

    # ----- lyrics & advisory -----
    if tags.lyrics:
        id3.add(USLT(encoding=3, lang="eng", desc="", text=tags.lyrics))
    if tags.advisory is not None:
        id3.add(TXXX(encoding=3, desc="ITUNESADVISORY", text=[str(tags.advisory)]))
