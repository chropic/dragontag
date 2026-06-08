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

import re
import time
from dataclasses import dataclass, field
from typing import Any

import musicbrainzngs as mb

from ..config import settings
from ..tagging.schema import TrackTags

_configured = False


def _mb_retry(fn, *args, retries: int = 2, backoff: float = 2.0, **kwargs):
    """Call ``fn`` with exponential backoff on ``WebServiceError``.

    Re-raises after exhausting retries so callers that must succeed (e.g.
    ``fetch_release``) can let the pipeline's outer handler surface the error.
    """
    for attempt in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except mb.WebServiceError:
            if attempt == retries:
                raise
            time.sleep(backoff * (2 ** attempt))


def _ensure_configured() -> None:
    """One-time User-Agent / rate-limit setup. Called lazily so we pick up
    any UA change the user makes in settings without restarting."""
    global _configured
    if _configured:
        return
    s = settings()
    try:
        from importlib.metadata import version as _pkg_version
        _version = _pkg_version("dragontag")
    except Exception:
        _version = "0.1.5"
    mb.set_useragent("dragontag", _version, s.musicbrainz_user_agent)
    mb.set_hostname(s.musicbrainz_server)
    mb.set_rate_limit(True)
    _configured = True


# Require a punctuation separator (. - )) after the leading number so we only
# strip genuine track-number prefixes ("01. ", "14-", "03 - ") and never a
# number that is part of the real title ("99 Luftballons", "7 Years").
_TRACK_NUM_PREFIX = re.compile(r"^\d+\s*[.\-)]+\s*")


def _strip_track_num(title: str) -> str:
    """Remove leading track numbers like '01. ' or '14-' from a title."""
    return _TRACK_NUM_PREFIX.sub("", title).strip()


@dataclass
class Candidate:
    """One MB search hit. Stored verbatim so we can:

    * Display it in the review UI without another network round trip.
    * Re-score it cheaply if the user tweaks the scoring weights.
    """

    score: float                 # MB's own 0..1 search relevance score
    recording_id: str
    release_id: str
    acoustid_id: str = ""        # non-empty when sourced from AcoustID fingerprint
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
    limit: int = 10,
) -> list[Candidate]:
    """Query MB recordings; return one ``Candidate`` per (recording, release) pair.

    Uses a progressive fallback strategy to maximise hit rate:
    1. title + artist + album + duration
    2. title + artist + duration (drop album if no results)
    3. title + artist only (drop duration if still no results)

    Leading track-number prefixes (e.g. "01. ", "14-") are stripped from the
    title before querying because they are not part of the MB recording title.
    """
    _ensure_configured()
    if not title:
        return []

    clean_title = _strip_track_num(title)

    def _run_query(include_album: bool, include_dur: bool) -> list[Candidate]:
        q_parts: list[str] = [f'recording:"{_escape(clean_title)}"']
        if artist:
            q_parts.append(f'artist:"{_escape(artist)}"')
        if album and include_album:
            q_parts.append(f'release:"{_escape(album)}"')
        if duration_sec and include_dur:
            ms = int(duration_sec * 1000)
            q_parts.append(f"dur:[{ms - 2000} TO {ms + 2000}]")
        try:
            res = _mb_retry(mb.search_recordings, query=" AND ".join(q_parts), limit=limit)
        except mb.WebServiceError:
            return []
        out: list[Candidate] = []
        for rec in res.get("recording-list", []):
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

    results = _run_query(include_album=True, include_dur=True)
    # Only retry without album if album was actually part of the first query —
    # otherwise the second call is identical to the first (wasted MB request).
    if not results and album:
        results = _run_query(include_album=False, include_dur=True)
    # Same for duration: skip the third attempt if duration wasn't available.
    if not results and duration_sec:
        results = _run_query(include_album=False, include_dur=False)
    return results


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
    return _mb_retry(
        mb.get_release_by_id,
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
    return _mb_retry(
        mb.get_recording_by_id,
        recording_id,
        includes=[
            "artists",
            "isrcs",
            "releases",
            "artist-credits",
            "artist-rels",
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
    tags.album_artists = [
        c["artist"]["name"] for c in rel_credits if isinstance(c, dict) and "artist" in c
    ]
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

    # Secondary fallback: match by recording title when the recording UUID
    # isn't in the track-list (rare MB data inconsistency).
    if track_position is None:
        rec_title = rec.get("title")
        for medium in rel.get("medium-list") or []:
            for trk in medium.get("track-list") or []:
                if trk.get("title") == rec_title and trk.get("position"):
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

    # ----- labels / catalog number / barcode / country / status / script / language -----
    labels: list[str] = []
    for li in rel.get("label-info-list") or []:
        if isinstance(li, dict):
            if li.get("label"):
                labels.append(li["label"].get("name"))
            if tags.catalog_number is None:
                cn = li.get("catalog-number")
                if cn:
                    tags.catalog_number = cn
    tags.labels = [x for x in labels if x]
    tags.barcode = rel.get("barcode") or None
    tags.release_country = rel.get("country") or None
    tags.release_status = rel.get("status") or None
    text_rep = rel.get("text-representation") or {}
    tags.script = text_rep.get("script")
    tags.language = text_rep.get("language") or None

    # ----- ISRCs (recording-level) -----
    tags.isrcs = list(rec.get("isrc-list") or [])

    # ----- compilation flag -----
    # True when release-group primary-type is Compilation or secondary-types include it.
    secondary_types = rg.get("secondary-type-list") or []
    tags.compilation = pt == "Compilation" or "Compilation" in secondary_types

    # ----- genre = top user-tags from recording or release-group -----
    # MB doesn't have a single canonical genre field; we use community-voted tags.
    cfg = settings()
    src_tags = rec.get("tag-list") or rg.get("tag-list") or []
    if src_tags:
        sorted_tags = sorted(src_tags, key=lambda t: int(t.get("count", 0)), reverse=True)
        limit = cfg.genre_limit if cfg.genre_limit > 0 else None
        raw_genres = [t["name"] for t in (sorted_tags[:limit] if limit else sorted_tags)]
        casing = cfg.genre_casing
        if casing == "lower":
            tags.genres = [g.lower() for g in raw_genres]
        elif casing == "as-is":
            tags.genres = raw_genres
        else:
            tags.genres = [g.title() for g in raw_genres]

    # ----- recording-level artist relations (conductor, etc.) -----
    for ar in rec.get("artist-relation-list") or []:
        name = (ar.get("artist") or {}).get("name")
        if name and ar.get("type") == "conductor":
            tags.conductor.append(name)

    # ----- roles via recording → work → artist relations -----
    composers: list[str] = []
    for wr in rec.get("work-relation-list") or []:
        for sub in (wr.get("work", {}) or {}).get("artist-relation-list") or []:
            rel_type = sub.get("type")
            name = (sub.get("artist") or {}).get("name")
            if not name:
                continue
            if rel_type == "composer":
                composers.append(name)
            elif rel_type == "lyricist":
                tags.lyricist.append(name)
            elif rel_type == "arranger":
                tags.arranger.append(name)
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
