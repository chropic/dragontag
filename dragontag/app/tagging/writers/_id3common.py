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


# Anything from the canonical Vorbis schema that doesn't have a dedicated
# ID3 frame goes through a TXXX:<NAME> frame. The names are kept identical
# to the Vorbis keys for cross-format consistency.
TXXX_FIELDS = (
    "ARTISTS",
    "ALBUMARTISTSORT",
    "ARTISTSORT",
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
    # we end with exactly the canonical set rather than a merge.
    id3.delete()
    v = tags.to_vorbis(sep)

    def add_frame(frame_cls, key: str) -> None:
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
    # TPE3 = conductor, TEXT = lyricist (standard ID3v2.4 frames)
    if tags.conductor:
        id3.add(TPE3(encoding=3, text=sep.CONDUCTOR.join(tags.conductor)))
    if tags.lyricist:
        id3.add(TEXT(encoding=3, text=sep.LYRICIST.join(tags.lyricist)))
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
        id3.add(
            APIC(
                encoding=3,
                mime=tags.cover_mime,
                type=3,  # front cover
                desc="Cover (front)",
                data=tags.cover_bytes,
            )
        )

    # ----- lyrics & advisory -----
    if tags.lyrics:
        id3.add(USLT(encoding=3, lang="eng", desc="", text=tags.lyrics))
    if tags.advisory is not None:
        id3.add(TXXX(encoding=3, desc="ITUNESADVISORY", text=[str(tags.advisory)]))
