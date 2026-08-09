"""MusicBrainz duplicate checks, validation, and official editor seeding.

MusicBrainz WS/2 remains read-only for core entities.  This module therefore
never claims to create an entity: it prepares an official web-editor handoff,
records what the user reports back, and verifies that result through WS/2.
Authentication belongs to the user's MusicBrainz browser session.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

import musicbrainzngs as mb

from ..config import settings
from ..identify import musicbrainz as lookup
from ..tagging.schema import TrackTags


MBID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
PARTIAL_DATE_RE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")


class ContributionValidationError(ValueError):
    pass


def valid_mbid(value: str | None) -> bool:
    return bool(value and MBID_RE.fullmatch(value.strip()))


def _text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _artists(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ContributionValidationError(f"{field} needs at least one artist credit")
    out: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict) or not _text(raw.get("name"), limit=500):
            raise ContributionValidationError(f"{field}[{index}] needs a name")
        item = {
            "name": _text(raw.get("name"), limit=500),
            "join_phrase": _text(raw.get("join_phrase"), limit=100),
        }
        mbid = _text(raw.get("mbid"), limit=36)
        if mbid:
            if not valid_mbid(mbid):
                raise ContributionValidationError(f"{field}[{index}] has an invalid MBID")
            item["mbid"] = mbid
        out.append(item)
    return out


def _partial_date(value: Any) -> str:
    raw = _text(value, limit=10)
    if not raw:
        return ""
    if not PARTIAL_DATE_RE.fullmatch(raw):
        raise ContributionValidationError("date must be YYYY, YYYY-MM, or YYYY-MM-DD")
    parts = [int(part) for part in raw.split("-")]
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError as exc:
        raise ContributionValidationError("date is not a real calendar date") from exc
    return raw


def _gtin(value: Any) -> str:
    raw = _text(value, limit=14)
    if not raw:
        return ""
    if not raw.isdigit() or len(raw) not in {8, 12, 13, 14}:
        raise ContributionValidationError("barcode must be an 8, 12, 13, or 14 digit GTIN")
    digits = [int(char) for char in raw]
    check = sum(
        digit * (3 if (len(digits) - 1 - index) % 2 == 1 else 1)
        for index, digit in enumerate(digits[:-1])
    )
    if (10 - check % 10) % 10 != digits[-1]:
        raise ContributionValidationError("barcode has an invalid GTIN check digit")
    return raw


def validate_release_draft(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContributionValidationError("release draft must be a JSON object")
    release = raw.get("release")
    media = raw.get("media")
    if not isinstance(release, dict):
        raise ContributionValidationError("release details are required")
    title = _text(release.get("title"), limit=500)
    if not title:
        raise ContributionValidationError("release title is required")
    artists = _artists(release.get("artists"), "release.artists")
    if not isinstance(media, list) or not media:
        raise ContributionValidationError("at least one medium is required")
    declared_medium_total = raw.get("medium_total")
    if declared_medium_total not in (None, ""):
        try:
            if int(declared_medium_total) != len(media):
                raise ContributionValidationError("medium_total must equal the number of media")
        except (TypeError, ValueError) as exc:
            raise ContributionValidationError("medium_total must be an integer") from exc

    normalized_media: list[dict[str, Any]] = []
    medium_positions: set[int] = set()
    for medium_index, medium_raw in enumerate(media):
        if not isinstance(medium_raw, dict):
            raise ContributionValidationError(f"media[{medium_index}] must be an object")
        try:
            position = int(medium_raw.get("position", medium_index + 1))
        except (TypeError, ValueError) as exc:
            raise ContributionValidationError(f"media[{medium_index}].position must be a number") from exc
        if position < 1 or position in medium_positions:
            raise ContributionValidationError("medium positions must be unique positive numbers")
        medium_positions.add(position)
        tracks = medium_raw.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            raise ContributionValidationError(f"medium {position} needs at least one track")
        declared_track_total = medium_raw.get("track_total")
        if declared_track_total not in (None, ""):
            try:
                if int(declared_track_total) != len(tracks):
                    raise ContributionValidationError(
                        f"medium {position} track_total must equal its track count"
                    )
            except (TypeError, ValueError) as exc:
                raise ContributionValidationError("track_total must be an integer") from exc
        medium_format = _text(medium_raw.get("format"), limit=100)
        if not medium_format:
            raise ContributionValidationError(f"medium {position} needs a format")
        normalized_tracks: list[dict[str, Any]] = []
        track_positions: set[int] = set()
        for track_index, track_raw in enumerate(tracks):
            if not isinstance(track_raw, dict):
                raise ContributionValidationError(f"medium {position} track {track_index + 1} is invalid")
            try:
                track_position = int(track_raw.get("position", track_index + 1))
            except (TypeError, ValueError) as exc:
                raise ContributionValidationError("track positions must be numbers") from exc
            track_title = _text(track_raw.get("title"), limit=500)
            if not track_title:
                raise ContributionValidationError(f"medium {position} track {track_position} needs a title")
            if track_position < 1 or track_position in track_positions:
                raise ContributionValidationError("track positions must be unique positive numbers per medium")
            track_positions.add(track_position)
            duration = track_raw.get("duration_ms")
            if duration in (None, ""):
                duration_ms = None
            else:
                try:
                    duration_ms = int(duration)
                except (TypeError, ValueError) as exc:
                    raise ContributionValidationError("track duration_ms must be an integer") from exc
                if duration_ms <= 0:
                    raise ContributionValidationError("track duration_ms must be positive")
            recording_mbid = _text(track_raw.get("recording_mbid"), limit=36)
            if recording_mbid and not valid_mbid(recording_mbid):
                raise ContributionValidationError("track recording_mbid is invalid")
            normalized_tracks.append({
                "position": track_position,
                "title": track_title,
                "artists": _artists(track_raw.get("artists") or artists, "track.artists"),
                "duration_ms": duration_ms,
                "recording_mbid": recording_mbid,
            })
        normalized_media.append({
            "position": position,
            "format": medium_format,
            "title": _text(medium_raw.get("title"), limit=500),
            "track_total": len(normalized_tracks),
            "tracks": sorted(normalized_tracks, key=lambda item: item["position"]),
        })

    release_group = release.get("release_group") or {}
    if not isinstance(release_group, dict):
        raise ContributionValidationError("release.release_group must be an object")
    rg_mbid = _text(release_group.get("mbid"), limit=36)
    if rg_mbid and not valid_mbid(rg_mbid):
        raise ContributionValidationError("release-group MBID is invalid")

    labels: list[dict[str, str]] = []
    for index, label_raw in enumerate(release.get("labels") or []):
        if not isinstance(label_raw, dict) or not _text(label_raw.get("name"), limit=500):
            raise ContributionValidationError(f"release.labels[{index}] needs a name")
        label = {
            "name": _text(label_raw.get("name"), limit=500),
            "catalog_number": _text(label_raw.get("catalog_number"), limit=200),
        }
        mbid = _text(label_raw.get("mbid"), limit=36)
        if mbid:
            if not valid_mbid(mbid):
                raise ContributionValidationError(f"release.labels[{index}] has an invalid MBID")
            label["mbid"] = mbid
        labels.append(label)

    country = _text(release.get("country"), limit=10).upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ContributionValidationError("country must be a two-letter code")
    edit_note = _text(raw.get("edit_note"), limit=5000)
    provenance = _text(raw.get("provenance"), limit=2000)
    if not provenance:
        raise ContributionValidationError("provenance/source is required")
    if not edit_note:
        raise ContributionValidationError("edit note is required")
    release_type = _text(release.get("type"), limit=100)
    if release_type.casefold() not in {"album", "single", "ep", "broadcast", "other"}:
        raise ContributionValidationError("release type must be Album, Single, EP, Broadcast, or Other")
    release_status = _text(release.get("status"), limit=100)
    if release_status.casefold() not in {
        "official", "promotion", "bootleg", "pseudo-release", "withdrawn",
        "expunged", "cancelled",
    }:
        raise ContributionValidationError("release status is not recognized")

    return {
        "release": {
            "title": title,
            "artists": artists,
            "release_group": {
                "title": _text(release_group.get("title"), limit=500) or title,
                "mbid": rg_mbid,
            },
            "date": _partial_date(release.get("date")),
            "country": country,
            "type": release_type,
            "status": release_status,
            "labels": labels,
            "barcode": _gtin(release.get("barcode")),
        },
        "medium_total": len(normalized_media),
        "media": sorted(normalized_media, key=lambda item: item["position"]),
        "provenance": provenance,
        "edit_note": edit_note,
    }


def validate_standalone_draft(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContributionValidationError("standalone draft must be a JSON object")
    title = _text(raw.get("title"), limit=500)
    if not title:
        raise ContributionValidationError("recording title is required")
    artists = _artists(raw.get("artists"), "artists")
    duration = raw.get("duration_ms")
    if duration in (None, ""):
        duration_ms = None
    else:
        try:
            duration_ms = int(duration)
        except (TypeError, ValueError) as exc:
            raise ContributionValidationError("duration_ms must be an integer") from exc
        if duration_ms <= 0:
            raise ContributionValidationError("duration_ms must be positive")
    isrc = _text(raw.get("isrc"), limit=12).upper().replace("-", "")
    if isrc and not ISRC_RE.fullmatch(isrc):
        raise ContributionValidationError("ISRC must use the 12-character CCXXXYYNNNNN form")
    source = _text(raw.get("source"), limit=2000)
    edit_note = _text(raw.get("edit_note"), limit=5000)
    if not source:
        raise ContributionValidationError("source/provenance is required")
    if not edit_note:
        raise ContributionValidationError("edit note is required")
    return {
        "title": title,
        "artists": artists,
        "duration_ms": duration_ms,
        "disambiguation": _text(raw.get("disambiguation"), limit=500),
        "isrc": isrc,
        "source": source,
        "edit_note": edit_note,
    }


def validate_draft(mode: str, raw: Any) -> dict[str, Any]:
    if mode == "release":
        return validate_release_draft(raw)
    if mode == "standalone":
        return validate_standalone_draft(raw)
    raise ContributionValidationError("mode must be release or standalone")


def _search(fn: Callable[..., dict], *, query: str, key: str) -> list[dict[str, Any]]:
    response = lookup._mb_retry(fn, query=query, limit=5, retries=1)
    rows = response.get(key, []) or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        try:
            score = float(row.get("ext:score", 0)) / 100.0
        except (TypeError, ValueError):
            score = 0.0
        name = row.get("name") or row.get("title") or ""
        out.append({
            "id": row["id"],
            "name": name,
            "score": score,
            "disambiguation": row.get("disambiguation") or "",
            "plausible": score >= 0.8,
        })
    return out


def search_duplicates(snapshot: dict[str, Any], mode: str, ctx=None) -> dict[str, Any]:
    """Search every entity class represented in the draft."""
    lookup._ensure_configured()
    result: dict[str, Any] = {"artists": {}, "recordings": {}, "releases": {},
                              "release_groups": {}, "labels": {}}

    def check() -> None:
        if ctx is not None:
            ctx.check_cancelled()

    artist_names: list[str] = []
    recording_titles: list[str] = []
    if mode == "release":
        release = snapshot["release"]
        artist_names.extend(artist["name"] for artist in release["artists"])
        for medium in snapshot["media"]:
            for track in medium["tracks"]:
                recording_titles.append(track["title"])
                artist_names.extend(artist["name"] for artist in track["artists"])
        release_title = release["title"]
        rg_title = release["release_group"]["title"]
        label_names = [label["name"] for label in release["labels"]]
    else:
        artist_names.extend(artist["name"] for artist in snapshot["artists"])
        recording_titles.append(snapshot["title"])
        release_title = rg_title = ""
        label_names = []

    total = len(set(artist_names)) + len(set(recording_titles)) + bool(release_title) + bool(rg_title) + len(set(label_names))
    current = 0
    for name in dict.fromkeys(artist_names):
        check(); current += 1
        if ctx: ctx.progress(current, total, f'artist "{name}"')
        result["artists"][name] = _search(mb.search_artists, query=f'artist:"{lookup._escape(name)}"', key="artist-list")
    for title in dict.fromkeys(recording_titles):
        check(); current += 1
        if ctx: ctx.progress(current, total, f'recording "{title}"')
        result["recordings"][title] = _search(mb.search_recordings, query=f'recording:"{lookup._escape(title)}"', key="recording-list")
    if release_title:
        check(); current += 1
        if ctx: ctx.progress(current, total, f'release "{release_title}"')
        result["releases"][release_title] = _search(mb.search_releases, query=f'release:"{lookup._escape(release_title)}"', key="release-list")
    if rg_title:
        check(); current += 1
        if ctx: ctx.progress(current, total, f'release group "{rg_title}"')
        result["release_groups"][rg_title] = _search(mb.search_release_groups, query=f'releasegroup:"{lookup._escape(rg_title)}"', key="release-group-list")
    for name in dict.fromkeys(label_names):
        check(); current += 1
        if ctx: ctx.progress(current, total, f'label "{name}"')
        result["labels"][name] = _search(mb.search_labels, query=f'label:"{lookup._escape(name)}"', key="label-list")
    return result


def plausible_decision_keys(results: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for entity_type, searches in results.items():
        if not isinstance(searches, dict):
            continue
        for query, rows in searches.items():
            for row in rows if isinstance(rows, list) else []:
                if row.get("plausible"):
                    keys.append(f"{entity_type}:{query}:{row.get('id', '')}")
    return keys


def validate_decisions(results: dict[str, Any], decisions: Any) -> dict[str, str]:
    if not isinstance(decisions, dict):
        decisions = {}
    required = plausible_decision_keys(results)
    allowed = {"reuse", "new", "different_edition"}
    clean = {str(key): str(value) for key, value in decisions.items() if str(value) in allowed}
    missing = [key for key in required if key not in clean]
    if missing:
        raise ContributionValidationError(
            f"decide reuse/new/different-edition for {len(missing)} plausible duplicate(s)"
        )
    return clean


def reused_id(decisions: dict[str, str], entity_type: str, query: str) -> str:
    prefix = f"{entity_type}:{query}:"
    for key, decision in decisions.items():
        if decision == "reuse" and key.startswith(prefix):
            return key[len(prefix):]
    return ""


def _add(payload: dict[str, list[str]], key: str, value: Any) -> None:
    text = _text(value)
    if text:
        payload.setdefault(key, []).append(text)


def build_release_seed_payload(
    snapshot: dict[str, Any], decisions: dict[str, str], redirect_uri: str
) -> dict[str, list[str]]:
    release = snapshot["release"]
    payload: dict[str, list[str]] = {}
    _add(payload, "name", release["title"])
    for index, artist in enumerate(release["artists"]):
        reused = artist.get("mbid") or reused_id(decisions, "artists", artist["name"])
        if reused:
            _add(payload, f"artist_credit.names.{index}.mbid", reused)
        else:
            _add(payload, f"artist_credit.names.{index}.name", artist["name"])
        _add(payload, f"artist_credit.names.{index}.join_phrase", artist.get("join_phrase"))
    rg = release["release_group"]
    rg_id = rg.get("mbid") or reused_id(decisions, "release_groups", rg["title"])
    if rg_id:
        _add(payload, "release_group", rg_id)
    else:
        _add(payload, "release_group.name", rg["title"])
    _add(payload, "type", release.get("type"))
    _add(payload, "status", release.get("status"))
    _add(payload, "country", release.get("country"))
    _add(payload, "barcode", release.get("barcode"))
    date_value = release.get("date") or ""
    for index, part in enumerate(date_value.split("-")):
        if part:
            _add(payload, ("events.0.date.year", "events.0.date.month", "events.0.date.day")[index], part)
    for index, label in enumerate(release.get("labels") or []):
        reused = label.get("mbid") or reused_id(decisions, "labels", label["name"])
        if reused:
            _add(payload, f"labels.{index}.mbid", reused)
        else:
            _add(payload, f"labels.{index}.name", label["name"])
        _add(payload, f"labels.{index}.catalog_number", label.get("catalog_number"))
    for medium_index, medium in enumerate(snapshot["media"]):
        _add(payload, f"mediums.{medium_index}.position", medium["position"])
        _add(payload, f"mediums.{medium_index}.format", medium.get("format"))
        _add(payload, f"mediums.{medium_index}.name", medium.get("title"))
        for track_index, track in enumerate(medium["tracks"]):
            prefix = f"mediums.{medium_index}.track.{track_index}"
            _add(payload, f"{prefix}.number", track["position"])
            _add(payload, f"{prefix}.name", track["title"])
            _add(payload, f"{prefix}.length", track.get("duration_ms"))
            rec_id = track.get("recording_mbid") or reused_id(decisions, "recordings", track["title"])
            if rec_id:
                _add(payload, f"{prefix}.recording", rec_id)
            for artist_index, artist in enumerate(track["artists"]):
                reused = artist.get("mbid") or reused_id(decisions, "artists", artist["name"])
                artist_prefix = f"{prefix}.artist_credit.names.{artist_index}"
                if reused:
                    _add(payload, f"{artist_prefix}.mbid", reused)
                else:
                    _add(payload, f"{artist_prefix}.name", artist["name"])
                _add(payload, f"{artist_prefix}.join_phrase", artist.get("join_phrase"))
    _add(payload, "edit_note", f"{snapshot['edit_note']}\n\nSource/provenance: {snapshot['provenance']}")
    _add(payload, "redirect_uri", redirect_uri)
    return payload


def build_standalone_seed_payload(
    snapshot: dict[str, Any], decisions: dict[str, str]
) -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    _add(payload, "edit-recording.name", snapshot["title"])
    _add(payload, "edit-recording.comment", snapshot.get("disambiguation"))
    _add(payload, "edit-recording.length", snapshot.get("duration_ms"))
    _add(payload, "edit-recording.isrc", snapshot.get("isrc"))
    for index, artist in enumerate(snapshot["artists"]):
        reused = artist.get("mbid") or reused_id(decisions, "artists", artist["name"])
        prefix = f"edit-recording.artist_credit.names.{index}"
        if reused:
            _add(payload, f"{prefix}.mbid", reused)
        else:
            _add(payload, f"{prefix}.name", artist["name"])
        _add(payload, f"{prefix}.join_phrase", artist.get("join_phrase"))
    _add(payload, "edit-recording.edit_note", f"{snapshot['edit_note']}\n\nSource/provenance: {snapshot['source']}")
    return payload


def editor_url(mode: str) -> str:
    host = settings().musicbrainz_server.strip().strip("/") or "musicbrainz.org"
    if host.startswith("http://") or host.startswith("https://"):
        base = host
    else:
        base = f"https://{host}"
    return f"{base}/release/add" if mode == "release" else f"{base}/recording/create"


def refresh_result(mode: str, snapshot: dict[str, Any], mbids: dict[str, str]) -> dict[str, Any]:
    """Fetch returned entities and create the exact metadata review preview."""
    if mode == "release":
        release_id = mbids.get("release") or ""
        if not valid_mbid(release_id):
            raise ContributionValidationError("a valid release MBID is required")
        rel = lookup.fetch_release(release_id)
        requested_recording = mbids.get("recording") or ""
        candidates: list[tuple[str, str]] = []
        for medium in rel.get("medium-list") or []:
            for track in medium.get("track-list") or []:
                rec = track.get("recording") or {}
                if rec.get("id"):
                    candidates.append((rec["id"], rec.get("title") or track.get("title") or ""))
        if requested_recording:
            matches = [item for item in candidates if item[0] == requested_recording]
        else:
            target = snapshot["media"][0]["tracks"][0]["title"].casefold()
            matches = [item for item in candidates if item[1].casefold() == target]
        if len(matches) != 1:
            return {
                "state": "ambiguous",
                "release_id": release_id,
                "release": rel.get("title") or "",
                "recording_choices": [{"id": rid, "title": title} for rid, title in candidates],
                "warning": "Choose the recording MBID for the local file, then refresh again.",
            }
        recording_id = matches[0][0]
        tags = lookup.assemble_tags(release_id=release_id, recording_id=recording_id, rel=rel)
    else:
        recording_id = mbids.get("recording") or ""
        if not valid_mbid(recording_id):
            raise ContributionValidationError("a valid recording MBID is required")
        rec = lookup.fetch_recording(recording_id)
        credits = rec.get("artist-credit") or []
        tags = TrackTags(
            title=rec.get("title"),
            artist_display=lookup._credit_phrase(credits),
            artists=lookup._credit_names(credits),
            artist_sort=lookup._credit_sorts(credits),
            mb_artist_ids=lookup._credit_ids(credits),
            isrcs=list(rec.get("isrc-list") or []),
            mb_track_id=recording_id,
            release_type="Single",
        )
        release_id = ""
    return {
        "state": "verified",
        "recording_id": recording_id,
        "release_id": release_id,
        "title": tags.title,
        "artist": tags.artist_display,
        "album": tags.album,
        "tags": {key: value for key, value in tags.__dict__.items() if key != "cover_bytes"},
    }
