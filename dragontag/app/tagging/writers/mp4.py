"""MP4 / M4A writer.

MP4 atoms use a small set of well-known fourcc names (``©nam``, ``©ART``,
``trkn``, etc.). For everything outside that set we use Apple's
``----:com.apple.iTunes:<NAME>`` freeform-atom convention. Picard does the
same, so MB IDs round-trip cleanly between Picard-tagged files and ours.

``trkn``/``disk`` are stored as tuples ``(number, total)`` — a value of 0
for total means "unknown".
"""
from __future__ import annotations

from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover

from ..schema import TrackTags

# Prefix for freeform atoms. Apple's ``----`` namespace + a mean (``com.apple.iTunes``)
# is the de-facto place for custom string fields.
_FREEFORM = "----:com.apple.iTunes:"


def _ff(name: str) -> str:
    return f"{_FREEFORM}{name}"


def write(path: Path, tags: TrackTags, sep) -> None:
    audio = MP4(str(path))
    audio.delete()  # clean slate — same reasoning as the FLAC writer

    # ``MP4.delete()`` removes the tag block; ensure we have one to write into.
    if audio.tags is None:
        audio.add_tags()

    t = audio.tags

    # ----- standard atoms -----
    if tags.title:
        t["\xa9nam"] = [tags.title]
    if tags.artist_display:
        t["\xa9ART"] = [tags.artist_display]
    if tags.album_artist_display:
        t["aART"] = [tags.album_artist_display]
    if tags.album:
        t["\xa9alb"] = [tags.album]
    if tags.composers:
        # MP4 has a single composer atom; join into one string with the
        # COMPOSER separator (default ``;``).
        t["\xa9wrt"] = [sep.COMPOSER.join(tags.composers)]
    if tags.genres:
        t["\xa9gen"] = [sep.GENRE.join(tags.genres)]
    if tags.date:
        t["\xa9day"] = [tags.date]

    if tags.track is not None:
        t["trkn"] = [(tags.track, tags.track_total or 0)]
    if tags.disc is not None:
        t["disk"] = [(tags.disc, tags.disc_total or 0)]

    # ----- freeform atoms for everything else -----
    def put_ff(name: str, value: str | None) -> None:
        if value is None or value == "":
            return
        # Freeform atoms expect bytes, not str.
        t[_ff(name)] = [value.encode("utf-8")]

    if tags.compilation:
        t["cpil"] = True

    v = tags.to_vorbis(sep)
    for k in (
        "ARTISTS",
        "ARTISTSORT",
        "ALBUMARTISTSORT",
        "RELEASECOUNTRY",
        "RELEASESTATUS",
        "RELEASETYPE",
        "SCRIPT",
        "BARCODE",
        "ORIGINALDATE",
        "ORIGINALYEAR",
        "LABEL",
        "MEDIA",
        "ISRC",
        "CATALOGNUMBER",
        "LANGUAGE",
        "CONDUCTOR",
        "LYRICIST",
        "ARRANGER",
        "ACOUSTID_ID",
        "MUSICBRAINZ_TRACKID",
        "MUSICBRAINZ_RELEASETRACKID",
        "MUSICBRAINZ_ALBUMID",
        "MUSICBRAINZ_ALBUMARTISTID",
        "MUSICBRAINZ_ARTISTID",
        "MUSICBRAINZ_RELEASEGROUPID",
        "TRACKTOTAL",
        "TOTALTRACKS",
        "DISCTOTAL",
        "TOTALDISCS",
    ):
        if k in v:
            put_ff(k, v[k])

    # ----- embedded front cover -----
    if tags.cover_bytes:
        fmt = MP4Cover.FORMAT_PNG if tags.cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
        t["covr"] = [MP4Cover(tags.cover_bytes, imageformat=fmt)]

    audio.save()
