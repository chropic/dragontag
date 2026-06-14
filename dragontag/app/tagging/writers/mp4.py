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
from ._atomic import atomic_inplace
from ._id3common import _cap_cover

# Prefix for freeform atoms. Apple's ``----`` namespace + a mean (``com.apple.iTunes``)
# is the de-facto place for custom string fields.
_FREEFORM = "----:com.apple.iTunes:"


def _ff(name: str) -> str:
    return f"{_FREEFORM}{name}"


def write(path: Path, tags: TrackTags, sep) -> None:
    with atomic_inplace(path) as tmp:
        audio = MP4(str(tmp))

        # Clean slate — same reasoning as the FLAC writer. ``tags.clear()``
        # wipes the atom set in memory (no extra on-disk write that
        # ``MP4.delete()`` would do); ensure a tag block exists first.
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()

        t = audio.tags

        # ----- standard atoms -----
        if tags.title:
            t["\xa9nam"] = [tags.title]
        # Artist / album-artist atoms are multi-value: one list entry per artist so
        # Navidrome / Picard see separate artists (not one "a; b" string).
        artists = tags.artists or ([tags.artist_display] if tags.artist_display else [])
        if artists:
            t["\xa9ART"] = artists
        album_artists = tags.album_artists or (
            [tags.album_artist_display] if tags.album_artist_display else []
        )
        if album_artists:
            t["aART"] = album_artists
        if tags.album:
            t["\xa9alb"] = [tags.album]
        if tags.composers:
            t["\xa9wrt"] = tags.composers
        if tags.genres:
            t["\xa9gen"] = tags.genres
        if tags.date:
            t["\xa9day"] = [tags.date]

        if tags.track is not None:
            t["trkn"] = [(tags.track, tags.track_total or 0)]
        if tags.disc is not None:
            t["disk"] = [(tags.disc, tags.disc_total or 0)]

        # ----- freeform atoms for everything else -----
        def put_ff(name: str, value: str | list[str] | None) -> None:
            # ``value`` may be a single string or a list of strings (multi-value
            # field). Freeform atoms expect bytes, and a list of byte-values is
            # written as a native multi-value freeform atom.
            if not value:
                return
            vals = value if isinstance(value, list) else [value]
            encoded = [s.encode("utf-8") for s in vals if s]
            if encoded:
                t[_ff(name)] = encoded

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
            "TAGGER",
        ):
            if k in v:
                put_ff(k, v[k])

        # ----- embedded front cover -----
        if tags.cover_bytes:
            cover_data, cover_mime = _cap_cover(tags.cover_bytes, tags.cover_mime or "image/jpeg")
            fmt = MP4Cover.FORMAT_PNG if "png" in cover_mime.lower() else MP4Cover.FORMAT_JPEG
            t["covr"] = [MP4Cover(cover_data, imageformat=fmt)]

        # ----- lyrics & advisory -----
        if tags.lyrics:
            t["\xa9lyr"] = [tags.lyrics]
        if tags.advisory is not None:
            t["rtng"] = [tags.advisory]

        audio.save()
