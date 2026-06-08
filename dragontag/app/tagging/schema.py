"""Canonical in-memory tag representation + Vorbis-Comment rendering.

This is the *only* place that knows the user's exact Vorbis convention from
``flac_metadata.md``. Other writers (MP3, MP4, WAV) re-use this dict by mapping
keys into their format's native frames/atoms (see ``writers/_id3common.py``
and ``writers/mp4.py``).

Notable convention details captured here:

* Lowercase keys for ``album_artist``, ``track``, ``disc`` — preserved as-is.
* Both ``TRACKTOTAL`` and ``TOTALTRACKS`` (and same for disc) are written —
  some players honor one but not the other.
* Multi-value tags (ARTIST, ARTISTS, GENRE, sort names, MB id lists, …) render
  as native multiple values — a ``list[str]`` that becomes one Vorbis comment
  per value (FLAC) / a multi-value frame or atom (ID3/MP4). This is what
  Navidrome / Picard split on; a single ``"a//b//c"`` string would be read as
  one artist. The per-tag ``Separators`` are therefore no longer used to join
  these fields.
* ``ARTIST``/``album_artist`` are the flat per-artist lists; the ``feat./&``
  display phrase (``artist_display``) is used only as a single-value fallback
  when the list is empty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import version as _pkg_version

try:
    _DRAGONTAG_VERSION = _pkg_version("dragontag")
except Exception:
    _DRAGONTAG_VERSION = "dev"


@dataclass
class TrackTags:
    """All the tag-shaped data for a single track.

    Optional fields default to ``None`` / empty list; the renderer omits
    anything that's missing so we don't write empty string tags.
    """

    # --- core identification ---
    title: str | None = None
    artist_display: str | None = None
    artists: list[str] = field(default_factory=list)
    artist_sort: list[str] = field(default_factory=list)
    album: str | None = None
    album_artist_display: str | None = None
    album_artists: list[str] = field(default_factory=list)
    album_artist_sort: list[str] = field(default_factory=list)
    composers: list[str] = field(default_factory=list)

    # --- dates ---
    date: str | None = None              # this release's date
    original_date: str | None = None     # release-group first-release-date
    original_year: str | None = None     # convenience year of the above

    # --- numbering ---
    track: int | None = None
    track_total: int | None = None
    disc: int | None = None
    disc_total: int | None = None

    # --- additional roles ---
    conductor: list[str] = field(default_factory=list)
    lyricist: list[str] = field(default_factory=list)
    arranger: list[str] = field(default_factory=list)

    # --- descriptive ---
    genres: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    media: str | None = None
    barcode: str | None = None
    isrcs: list[str] = field(default_factory=list)
    catalog_number: str | None = None
    language: str | None = None
    compilation: bool = False

    # --- MB release-level metadata ---
    release_country: str | None = None
    release_status: str | None = None
    release_type: str | None = None      # Album / Single / EP / Compilation / ...
    script: str | None = None

    # --- external IDs ---
    acoustid_id: str | None = None
    mb_track_id: str | None = None              # recording MBID
    mb_releasetrack_id: str | None = None       # track-on-this-release MBID
    mb_album_id: str | None = None              # release MBID
    mb_album_artist_ids: list[str] = field(default_factory=list)
    mb_artist_ids: list[str] = field(default_factory=list)
    mb_release_group_id: str | None = None

    # --- cover art (binary, not a tag in the usual sense) ---
    # Populated by ``tagging/coverart.py``; consumed by each format's writer
    # to embed an APIC / PICTURE / covr block.
    cover_bytes: bytes | None = None
    cover_mime: str = "image/jpeg"

    # --- lyrics & advisory ---
    # lyrics: plain text or LRC synced format; fetched by tagging/lyrics_fetcher.py
    # advisory: 0=clean, 1=explicit, None=no lyrics available
    lyrics: str | None = None
    advisory: int | None = None

    # --- tagger attribution ---
    tagger: str = field(default_factory=lambda: f"tagged via dragontag/{_DRAGONTAG_VERSION}")

    def to_vorbis(self, sep) -> dict[str, str | list[str]]:
        """Render to ``{VorbisFieldName: value}`` for FLAC/OGG writers.

        Single-value fields render as ``str``; genuinely multi-value fields
        (ARTIST, ARTISTS, GENRE, sort names, MB id lists, …) render as a
        ``list[str]`` so the FLAC writer emits one Vorbis comment *per value*
        and the ID3/MP4 writers emit native multi-value frames/atoms. That is
        what Navidrome / Picard split on — a single ``"a//b//c"`` string is read
        as one artist, which is the bug this avoids. ``sep`` (the per-tag joiner
        config) is accepted for call-site compatibility but no longer pre-joins
        these fields. Empty values are skipped so we never write blank tags.
        """

        d: dict[str, str | list[str]] = {}

        def put(k: str, v: str | None) -> None:
            if v is not None and v != "":
                d[k] = str(v)

        def put_list(k: str, values: list[str]) -> None:
            # Drop empties so we never emit a blank value among real ones.
            clean = [v for v in values if v]
            if clean:
                d[k] = clean

        # ----- basic identification -----
        put("TITLE", self.title)
        # ARTIST is multi-value (one Vorbis comment per artist); fall back to the
        # display phrase only when the flat list is unavailable.
        put_list("ARTIST", self.artists or ([self.artist_display] if self.artist_display else []))
        put_list("ARTISTS", self.artists)
        put_list("ARTISTSORT", self.artist_sort)
        put("ALBUM", self.album)
        # album_artist key is lowercase by the project's Vorbis convention.
        put_list(
            "album_artist",
            self.album_artists or ([self.album_artist_display] if self.album_artist_display else []),
        )
        put_list("ALBUMARTISTSORT", self.album_artist_sort)
        put_list("COMPOSER", self.composers)
        put_list("CONDUCTOR", self.conductor)
        put_list("LYRICIST", self.lyricist)
        put_list("ARRANGER", self.arranger)

        # ----- dates -----
        put("DATE", self.date)
        put("ORIGINALDATE", self.original_date)
        put("ORIGINALYEAR", self.original_year)

        # ----- numbering: NN/TT plus duplicated totals -----
        if self.track is not None:
            d["track"] = (
                f"{self.track:02d}/{self.track_total:02d}"
                if self.track_total else f"{self.track:02d}"
            )
        if self.track_total is not None:
            # Both names are written; different players read different ones.
            d["TRACKTOTAL"] = str(self.track_total)
            d["TOTALTRACKS"] = str(self.track_total)
        if self.disc is not None:
            d["disc"] = (
                f"{self.disc:02d}/{self.disc_total:02d}"
                if self.disc_total else f"{self.disc:02d}"
            )
        if self.disc_total is not None:
            d["DISCTOTAL"] = str(self.disc_total)
            d["TOTALDISCS"] = str(self.disc_total)

        # ----- descriptive -----
        put_list("GENRE", self.genres)
        put_list("LABEL", self.labels)
        put("MEDIA", self.media)
        put("BARCODE", self.barcode)
        put_list("ISRC", self.isrcs)
        put("CATALOGNUMBER", self.catalog_number)
        put("LANGUAGE", self.language)
        if self.compilation:
            d["COMPILATION"] = "1"

        # ----- MB release-level -----
        put("RELEASECOUNTRY", self.release_country)
        put("RELEASESTATUS", self.release_status)
        put("RELEASETYPE", self.release_type)
        put("SCRIPT", self.script)

        # ----- external IDs -----
        put("ACOUSTID_ID", self.acoustid_id)
        put("MUSICBRAINZ_TRACKID", self.mb_track_id)
        put("MUSICBRAINZ_RELEASETRACKID", self.mb_releasetrack_id)
        put("MUSICBRAINZ_ALBUMID", self.mb_album_id)
        put_list("MUSICBRAINZ_ALBUMARTISTID", self.mb_album_artist_ids)
        put_list("MUSICBRAINZ_ARTISTID", self.mb_artist_ids)
        put("MUSICBRAINZ_RELEASEGROUPID", self.mb_release_group_id)

        # ----- lyrics & advisory -----
        put("LYRICS", self.lyrics)
        if self.advisory is not None:
            d["ITUNESADVISORY"] = str(self.advisory)

        # ----- attribution -----
        put("TAGGER", self.tagger)

        return d
