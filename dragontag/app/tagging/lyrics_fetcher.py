"""LRCLIB lyrics client.

Fetches lyrics for a track by artist + title, returning synced LRC text when
available or plain text as a fallback.  All network errors are swallowed so
a lyrics miss never fails the pipeline.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_BASE = "https://lrclib.net/api"
_HEADERS = {"User-Agent": "dragontag/0.1 (https://github.com/chropic/dragontag)"}
_TIMEOUT = 10


def fetch(
    artist: str | None,
    title: str | None,
    album: str | None = None,
    duration: float | None = None,
) -> str | None:
    """Return synced LRC text, plain text, or None (not found / error / instrumental).

    Tries an exact-match lookup first (artist + title + optional album/duration),
    then falls back to a freetext search and takes the top result.
    """
    if not artist or not title:
        return None
    try:
        return _fetch_inner(artist, title, album, duration)
    except Exception as exc:
        log.debug("lyrics fetch error for %r / %r: %s", artist, title, exc)
        return None


def _fetch_inner(artist, title, album, duration) -> str | None:
    import requests
    params: dict = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration is not None:
        params["duration"] = int(duration)

    resp = requests.get(f"{_BASE}/get", params=params, headers=_HEADERS, timeout=_TIMEOUT)
    if resp.status_code == 200:
        result = _parse(resp.json())
        if result is not None:
            return result

    # Fallback: search endpoint. The /search results are ranked by relevance,
    # not exact-matched, so the top hit can be a *different* song — accepting it
    # blindly would embed the wrong lyrics (and skew the explicit classifier).
    # Take the first hit whose artist + title actually match the request.
    search_params = {"track_name": title, "artist_name": artist}
    resp = requests.get(f"{_BASE}/search", params=search_params, headers=_HEADERS, timeout=_TIMEOUT)
    if resp.status_code == 200:
        hits = resp.json()
        if isinstance(hits, list):
            for hit in hits:
                if _hit_matches(hit, artist, title):
                    return _parse(hit)

    return None


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _hit_matches(hit: dict, artist: str, title: str) -> bool:
    """True when a /search hit plausibly refers to the requested track.

    Conservative: the hit's track/artist names must equal the request after
    case/whitespace normalization (LRCLIB echoes both back on every hit).
    """
    return (
        _norm(hit.get("trackName")) == _norm(title)
        and _norm(hit.get("artistName")) == _norm(artist)
    )


def _parse(data: dict) -> str | None:
    if data.get("instrumental"):
        return None
    if data.get("syncedLyrics"):
        return data["syncedLyrics"]
    if data.get("plainLyrics"):
        return data["plainLyrics"]
    return None
