"""MusicBrainz client + ``TrackTags`` assembler.

This module is responsible for two phases:

1. **Search** — given a few clues (title/artist/album/duration), call MB's
   recording search and return the top N candidates with their raw payloads
   so the scoring layer can rank them without re-querying.

2. **Assemble** — given a chosen (recording_id, release_id) pair, fetch the
   full release + recording records and translate everything into a
   ``TrackTags`` instance ready for the writers.

We enable musicbrainzngs's built-in rate limiter (1 req/sec) because MB will
ban User-Agents that hammer the API. The User-Agent string is also pulled
from user settings — MB requires a contact URL/email in it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs as mb

from ..config import settings
from ..tagging.schema import TrackTags

_configured = False


def _ensure_configured() -> None:
    """One-time User-Agent / rate-limit setup. Called lazily so we pick up
    any UA change the user makes in settings without restarting."""
    global _configured
    if _configured:
        return
    s = settings()
    mb.set_useragent("aio-tagger", "0.1.0", s.musicbrainz_user_agent)
    mb.set_hostname(s.musicbrainz_server)
    mb.set_rate_limit(True)
    _configured = True


@dataclass
class Candidate:
    """One MB search hit. Stored verbatim so we can:

    * Display it in the review UI without another network round trip.
    * Re-score it cheaply if the user tweaks the scoring weights.
    """

    score: float                 # MB's own 0..1 search relevance score
    recording_id: str
    release_id: str
    medium: dict[str, Any] = field(default_factory=dict)
    track: dict[str, Any] = field(default_factory=dict)
    raw_recording: dict[str, Any] = field(default_factory=dict)
    raw_release: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_candidates(
    *,
    title: str | None,
    artist: str | None,
    album: str | None,
    duration_sec: float | None = None,
    limit: int = 5,
) -> list[Candidate]:
    """Query MB recordings; return one ``Candidate`` per (recording, release) pair.

    Returns ``[]`` if we have no title to search on (MB requires at least one
    indexed field, and title is the most reliable signal we have).

    The query is built in Lucene syntax. We escape only the minimum to prevent
    quote/backslash injection — over-aggressive escaping breaks artist names
    that contain legitimate special characters.
    """
    _ensure_configured()
    if not title:
        return []

    q_parts: list[str] = [f'recording:"{_escape(title)}"']
    if artist:
        q_parts.append(f'artist:"{_escape(artist)}"')
    if album:
        q_parts.append(f'release:"{_escape(album)}"')
    if duration_sec:
        # MB stores duration in ms; ±2s window allows for codec/encoder skew.
        ms = int(duration_sec * 1000)
        q_parts.append(f"dur:[{ms - 2000} TO {ms + 2000}]")

    query = " AND ".join(q_parts)
    try:
        res = mb.search_recordings(query=query, limit=limit)
    except mb.WebServiceError:
        # Transient MB failure — return empty so the pipeline can fall back
        # to AcoustID instead of raising.
        return []

    out: list[Candidate] = []
    for rec in res.get("recording-list", []):
        # MB returns recordings paired with the releases they appear on.
        # We expand to one Candidate per (recording, release) so each can
        # be ranked separately.
        for rel in rec.get("release-list", []) or []:
            out.append(
                Candidate(
                    score=float(rec.get("ext:score", 0)) / 100.0,
                    recording_id=rec["id"],
                    release_id=rel["id"],
                    raw_recording=rec,
                    raw_release=rel,
                )
            )
    return out


def _escape(s: str) -> str:
    # Drop backslashes and quotes so they can't terminate the field clause.
    # Leaving everything else (parens, hyphens, etc.) intact preserves Lucene's
    # ability to match special-character artist names.
    return s.replace("\\", "").replace('"', "")


# ---------------------------------------------------------------------------
# Full fetch
# ---------------------------------------------------------------------------


def fetch_release(release_id: str) -> dict[str, Any]:
    """Fetch a release with every include the assembler needs.

    The include list determines what data is in the response — missing one
    here translates into a missing tag in the final file, so the list is
    intentionally generous.
    """
    _ensure_configured()
    return mb.get_release_by_id(
        release_id,
        includes=[
            "artists",
            "labels",
            "recordings",
            "release-groups",
            "artist-credits",
            "isrcs",
            "media",
            "discids",
        ],
    )["release"]


def fetch_recording(recording_id: str) -> dict[str, Any]:
    _ensure_configured()
    return mb.get_recording_by_id(
        recording_id,
        includes=[
            "artists",
            "isrcs",
            "releases",
            "release-groups",
            "artist-credits",
            "work-rels",
            "work-level-rels",
            "tags",
        ],
    )["recording"]


# ---------------------------------------------------------------------------
# Assemble TrackTags from a (recording_id, release_id) pair
# ---------------------------------------------------------------------------


def assemble_tags(*, release_id: str, recording_id: str) -> TrackTags:
    """Build a ``TrackTags`` from an MB release + recording.

    This is the core translation step from "MB-shaped data" to "our schema".
    Anything fancy in the user's tagging convention (the duplicated track
    totals, the lowercase Vorbis keys, etc.) is handled later in
    ``TrackTags.to_vorbis()``; here we just populate fields.
    """
    rel = fetch_release(release_id)
    rec = fetch_recording(recording_id)

    tags = TrackTags()
    tags.title = rec.get("title")

    # ----- recording-level artist credits -----
    # The artist-credit array preserves order + joinphrases (" feat. ", " & ").
    # We capture the joined phrase for ``ARTIST`` and the flat list for ``ARTISTS``.
    rec_credits = rec.get("artist-credit") or []
    tags.artist_display = _credit_phrase(rec_credits)
    tags.artists = [
        c["artist"]["name"] for c in rec_credits if isinstance(c, dict) and "artist" in c
    ]
    tags.artist_sort = [
        c["artist"].get("sort-name", c["artist"]["name"])
        for c in rec_credits
        if isinstance(c, dict) and "artist" in c
    ]
    tags.mb_artist_ids = [
        c["artist"]["id"] for c in rec_credits if isinstance(c, dict) and "artist" in c
    ]

    # ----- release-level (album) -----
    tags.album = rel.get("title")
    rel_credits = rel.get("artist-credit") or []
    tags.album_artist_display = _credit_phrase(rel_credits)
    tags.album_artist_sort = [
        c["artist"].get("sort-name", c["artist"]["name"])
        for c in rel_credits
        if isinstance(c, dict) and "artist" in c
    ]
    tags.mb_album_artist_ids = [
        c["artist"]["id"] for c in rel_credits if isinstance(c, dict) and "artist" in c
    ]

    # ----- find the specific track within the release -----
    # A release has N media (discs); each medium has a list of tracks. The
    # *same* recording can appear on multiple releases or even multiple
    # media within one release, so we have to scan to find the right slot.
    track_position = None
    disc_position = None
    track_total = None
    disc_total = len(rel.get("medium-list") or []) or None
    media_format = None
    mb_releasetrack_id = None
    for medium in rel.get("medium-list") or []:
        for trk in medium.get("track-list") or []:
            if trk.get("recording", {}).get("id") == recording_id:
                track_position = int(trk["position"])
                disc_position = int(medium.get("position", 1))
                track_total = int(
                    medium.get("track-count") or len(medium.get("track-list") or [])
                )
                media_format = medium.get("format")
                mb_releasetrack_id = trk.get("id")
                break
        if track_position is not None:
            break

    tags.track = track_position
    tags.track_total = track_total
    tags.disc = disc_position
    tags.disc_total = disc_total
    tags.media = media_format
    tags.mb_releasetrack_id = mb_releasetrack_id

    # ----- dates -----
    # DATE = this specific release's date (e.g. a 2014 reissue).
    # ORIGINALDATE = release-group first-release-date (the original 1972 issue).
    tags.date = rel.get("date")
    rg = rel.get("release-group") or {}
    tags.original_date = rg.get("first-release-date") or rel.get("date")
    if tags.original_date and len(tags.original_date) >= 4:
        tags.original_year = tags.original_date[:4]
    tags.mb_release_group_id = rg.get("id")
    # primary-type is e.g. "Album"/"Single"/"EP". If absent, the pipeline
    # routes the job to the review queue for a manual override.
    pt = rg.get("primary-type") or rg.get("type")
    tags.release_type = pt

    # ----- labels / barcode / country / status / script -----
    labels: list[str] = []
    for li in rel.get("label-info-list") or []:
        if isinstance(li, dict) and li.get("label"):
            labels.append(li["label"].get("name"))
    tags.labels = [x for x in labels if x]
    tags.barcode = rel.get("barcode") or None
    tags.release_country = rel.get("country") or None
    tags.release_status = rel.get("status") or None
    tags.script = (rel.get("text-representation") or {}).get("script")

    # ----- ISRCs (recording-level) -----
    tags.isrcs = list(rec.get("isrc-list") or [])

    # ----- genre = top user-tag from recording or release-group -----
    # MB doesn't have a single canonical genre field; we use the
    # community-voted tag with the highest count.
    src_tags = rec.get("tag-list") or rg.get("tag-list") or []
    if src_tags:
        sorted_tags = sorted(src_tags, key=lambda t: int(t.get("count", 0)), reverse=True)
        # Title-case for display consistency (MB tags are usually lowercase).
        tags.genres = [t["name"].title() for t in sorted_tags[:3]]

    # ----- composers via recording → work → composer relation -----
    composers: list[str] = []
    for wr in rec.get("work-relation-list") or []:
        for sub in (wr.get("work", {}) or {}).get("artist-relation-list") or []:
            if sub.get("type") == "composer":
                name = sub.get("artist", {}).get("name")
                if name:
                    composers.append(name)
    tags.composers = composers

    tags.mb_track_id = recording_id
    tags.mb_album_id = release_id
    return tags


def _credit_phrase(credits: list[Any]) -> str | None:
    """Render an MB artist-credit list back into its display phrase.

    Each credit item has either a ``name`` (override of the artist's canonical
    name) and a ``joinphrase`` (e.g. ``" feat. "``). Concatenating them in
    order reconstructs strings like ``"Bladee feat. Thaiboy Digital"``.
    """
    if not credits:
        return None
    out: list[str] = []
    for c in credits:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict):
            name = c.get("name") or c.get("artist", {}).get("name")
            if name:
                out.append(name)
            jp = c.get("joinphrase")
            if jp:
                out.append(jp)
    s = "".join(out).strip()
    return s or None
