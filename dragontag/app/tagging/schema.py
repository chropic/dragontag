"""Canonical in-memory tag representation + Vorbis-Comment rendering.

This is the *only* place that knows the user's exact Vorbis convention from
``flac_metadata.md``. Other writers (MP3, MP4, WAV) re-use this dict by mapping
keys into their format's native frames/atoms (see ``writers/_id3common.py``
and ``writers/mp4.py``).

Notable convention details captured here:

* Lowercase keys for ``album_artist``, ``track``, ``disc`` — preserved as-is.
* Both ``TRACKTOTAL`` and ``TOTALTRACKS`` (and same for disc) are written —
  some players honor one but not the other.
* Multi-value tags are joined into a single string per Vorbis convention,
  with per-tag separators (default ``//`` for ARTIST, ``;`` elsewhere).
* ``ARTIST`` is the *display phrase* including ``feat.`` joinphrases when MB
  has them; ``ARTISTS`` is the flat ordered list. ``artist_display`` is the
  pre-joined string from MB's ``artist-credit``; if absent we fall back to
  joining ``artists`` with the ``ARTIST`` separator.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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

    def to_vorbis(self, sep) -> dict[str, str]:
        """Render to ``{VorbisFieldName: string-value}`` for FLAC/OGG writers.

        ``sep`` is a ``Separators`` model (per-tag joiner config). The method
        skips empty values entirely so we don't pollute files with blank tags.
        """

        def join(values: list[str], joiner: str) -> str:
            # Drop empties before joining so we never produce ``"A;;B"``.
            return joiner.join(v for v in values if v)

        d: dict[str, str] = {}

        def put(k: str, v: str | None) -> None:
            if v is not None and v != "":
                d[k] = str(v)

        # ----- basic identification -----
        put("TITLE", self.title)
        # ARTIST is a single display string; fall back to joining ``artists``
        # if the caller didn't pre-compute one (rare — MB always provides it).
        put("ARTIST", self.artist_display or join(self.artists, sep.ARTIST))
        if self.artists:
            d["ARTISTS"] = join(self.artists, sep.ARTISTS)
        if self.artist_sort:
            d["ARTISTSORT"] = join(self.artist_sort, sep.ARTISTSORT)
        put("ALBUM", self.album)
        put("album_artist", self.album_artist_display)  # lowercase by convention
        if self.album_artist_sort:
            d["ALBUMARTISTSORT"] = join(self.album_artist_sort, sep.ALBUMARTISTSORT)
        if self.composers:
            d["COMPOSER"] = join(self.composers, sep.COMPOSER)
        if self.conductor:
            d["CONDUCTOR"] = join(self.conductor, sep.CONDUCTOR)
        if self.lyricist:
            d["LYRICIST"] = join(self.lyricist, sep.LYRICIST)
        if self.arranger:
            d["ARRANGER"] = join(self.arranger, sep.ARRANGER)

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
        if self.genres:
            d["GENRE"] = join(self.genres, sep.GENRE)
        if self.labels:
            d["LABEL"] = join(self.labels, sep.LABEL)
        put("MEDIA", self.media)
        put("BARCODE", self.barcode)
        if self.isrcs:
            d["ISRC"] = join(self.isrcs, sep.ISRC)
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
        if self.mb_album_artist_ids:
            d["MUSICBRAINZ_ALBUMARTISTID"] = join(
                self.mb_album_artist_ids, sep.MUSICBRAINZ_ALBUMARTISTID
            )
        if self.mb_artist_ids:
            d["MUSICBRAINZ_ARTISTID"] = join(
                self.mb_artist_ids, sep.MUSICBRAINZ_ARTISTID
            )
        put("MUSICBRAINZ_RELEASEGROUPID", self.mb_release_group_id)

        # ----- lyrics & advisory -----
        put("LYRICS", self.lyrics)
        if self.advisory is not None:
            d["ITUNESADVISORY"] = str(self.advisory)

        return d
