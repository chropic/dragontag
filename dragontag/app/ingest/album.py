"""Album-group release election.

Per-track identification is what splits albums: each file independently
matches whichever edition of an album scores a hair higher for *it*, so one
folder ends up scattered across several MUSICBRAINZ_ALBUMIDs (and DATE,
RELEASESTATUS, MEDIA, ALBUMARTIST… diverge with it) — players then render the
album as multiple listings.

This module fixes that at the source: jobs enqueued from one album folder
share a ``Job.group_key``, and the pipeline asks here for ONE elected
MusicBrainz release for the whole group. Every member is then assembled from
that single release document, so all release-level tags are identical across
the album by construction.

The election is a pure computation over the group's file clues plus
MusicBrainz lookups; it commits nothing. Results are memoized per group key
(the single worker thread is the only consumer) and recomputed whenever the
group's membership changes — the drop watcher enqueues an album's files one
by one over time, so later members must be able to join an existing election.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from ..config import settings
from ..db import session
from ..identify import existing_tags, filename_parse
from ..identify import musicbrainz as mbq
from ..identify.scoring import _sim
from ..models import Job

log = logging.getLogger(__name__)

# How many candidate releases (by vote frequency) get a full MB fetch per
# election. Pre-existing per-file album ids are always fetched on top — they
# are strong evidence, just no longer an automatic per-file winner (per-file
# MBID short-circuits are exactly the drift that split albums).
_MAX_CANDIDATE_FETCHES = 8

# A file counts as "on" a candidate release only when its best track match
# clears this floor — below it the similarity is noise, not a match.
_MATCH_FLOOR = 0.55


@dataclass
class GroupElection:
    """The outcome of electing one release for an album group."""

    release_id: str
    release_doc: dict                    # full fetch_release document (media+tracks)
    recording_by_job: dict[int, str]     # job_id -> recording_id on the elected release
    score: float                         # group confidence: coverage x mean match score
    unmatched_job_ids: set[int]          # members not on the elected release
    member_job_ids: frozenset[int] = field(default_factory=frozenset)


_elections: dict[str, GroupElection] = {}


def get_or_elect(group_key: str) -> GroupElection | None:
    """Memoized election, recomputed when NEW members join the group.

    Members leaving the active set (each job completes right after consuming
    the election) must NOT invalidate it — recomputing over the shrinking
    remainder would change coverage (and the score) mid-group, splitting one
    album across two verdicts. Only an unseen member (the watcher settling
    another file of the album) forces a recompute. The memo is process-local;
    a restart simply recomputes.
    """
    member_ids = frozenset(j.id for j in _load_group_jobs(group_key))
    cached = _elections.get(group_key)
    if cached is not None and member_ids <= cached.member_job_ids:
        return cached
    election = elect_release(group_key)
    if election is not None:
        _elections[group_key] = election
    else:
        _elections.pop(group_key, None)
    return election


def invalidate(group_key: str) -> None:
    _elections.pop(group_key, None)


def _load_group_jobs(group_key: str) -> list[Job]:
    from .pipeline import _ACTIVE_STATUSES

    with session() as s:
        return list(
            s.exec(
                select(Job).where(
                    Job.group_key == group_key,
                    Job.status.in_(_ACTIVE_STATUSES),
                )
            ).all()
        )


def _job_clues(job: Job) -> dict | None:
    """Read identification clues for one member file, or None if unreadable."""
    src = Path(job.source_path)
    if not src.exists():
        if job.destination_path and Path(job.destination_path).exists():
            src = Path(job.destination_path)
        else:
            return None
    try:
        existing = existing_tags.read(src)
    except Exception:
        existing = {}
    fname = filename_parse.parse(src)
    return {
        "title": existing.get("title") or fname.get("title"),
        "artist": existing.get("artist") or fname.get("artist"),
        "album": existing.get("album"),
        "duration": existing.get("duration"),
        "track": _parse_track_num(existing.get("track")),
        "mb_album_id": existing.get("mb_album_id"),
    }


def _parse_track_num(raw) -> int | None:
    """'3', '3/12', 3 -> 3; anything unparsable -> None."""
    if raw is None:
        return None
    try:
        return int(str(raw).split("/")[0])
    except (TypeError, ValueError):
        return None


def _match_file_to_release(clues: dict, rel: dict) -> tuple[float, str | None]:
    """Best (score, recording_id) for one file against one release document.

    Per-track score leans on title similarity, with duration and track-number
    agreement as tie-breakers — the release-level fields (artist/album) are
    already implied by the candidate release itself.
    """
    best_score = 0.0
    best_rec: str | None = None
    for medium in rel.get("medium-list") or []:
        for trk in medium.get("track-list") or []:
            rec = trk.get("recording") or {}
            rec_id = rec.get("id")
            if not rec_id:
                continue
            title_sim = _sim(
                trk.get("title") or rec.get("title"), clues.get("title")
            )
            duration = 0.0
            src_dur = clues.get("duration")
            length_ms = rec.get("length") or trk.get("length")
            if src_dur is not None and length_ms is not None:
                try:
                    delta = abs(float(length_ms) / 1000.0 - float(src_dur))
                    duration = max(0.0, 1.0 - delta / 5.0)
                except (TypeError, ValueError):
                    duration = 0.0
            tracknum = 0.0
            if clues.get("track") is not None and trk.get("position"):
                try:
                    tracknum = 1.0 if int(trk["position"]) == clues["track"] else 0.0
                except (TypeError, ValueError):
                    tracknum = 0.0
            score = 0.6 * title_sim + 0.25 * duration + 0.15 * tracknum
            if score > best_score:
                best_score, best_rec = score, rec_id
    return best_score, best_rec


def _candidate_release_ids(clue_by_job: dict[int, dict]) -> list[str]:
    """Candidate releases for the group: per-file MB text search hits plus any
    pre-existing per-file album ids (demoted from short-circuit to strongly
    weighted candidate), with an AcoustID fallback for files the text search
    can't see. Ordered by vote frequency, capped, existing ids always kept."""
    votes: Counter[str] = Counter()
    existing_ids: set[str] = set()

    for clues in clue_by_job.values():
        if clues.get("mb_album_id"):
            existing_ids.add(clues["mb_album_id"])
            votes[clues["mb_album_id"]] += 2  # strong, but not decisive
        cands = mbq.search_candidates(
            title=clues.get("title"),
            artist=clues.get("artist"),
            album=clues.get("album"),
            duration_sec=clues.get("duration"),
            limit=5,
        )
        if not cands and settings().acoustid_enabled and clues.get("_path"):
            from ..identify import acoustid as acid

            for m in acid.lookup(Path(clues["_path"]))[:2]:
                if not m.recording_id:
                    continue
                try:
                    rec = mbq.fetch_recording(m.recording_id)
                except Exception:
                    continue
                for rel in (rec.get("release-list") or [])[:3]:
                    votes[rel["id"]] += 1
        for c in cands:
            votes[c.release_id] += 1

    ranked = [rid for rid, _ in votes.most_common(_MAX_CANDIDATE_FETCHES)]
    for rid in sorted(existing_ids):
        if rid not in ranked:
            ranked.append(rid)
    return ranked


def _library_majority_release(rel: dict) -> bool:
    """Does this release match the library's majority edition of its group?"""
    from .pipeline import _existing_release_for_group

    rg_id = (rel.get("release-group") or {}).get("id")
    if not rg_id:
        return False
    return _existing_release_for_group(rg_id) == rel.get("id")


def elect_release(group_key: str) -> GroupElection | None:
    """Elect one release for the group, or None when no election is possible
    (no readable members, MusicBrainz unreachable, zero candidates) — the
    caller falls back to per-track identification."""
    jobs = _load_group_jobs(group_key)
    clue_by_job: dict[int, dict] = {}
    for j in jobs:
        clues = _job_clues(j)
        if clues is not None:
            clues["_path"] = j.source_path
            clue_by_job[j.id] = clues
    if not clue_by_job:
        return None

    try:
        candidate_ids = _candidate_release_ids(clue_by_job)
    except Exception:
        log.exception("album election: candidate gathering failed for %s", group_key)
        return None
    if not candidate_ids:
        return None

    best: tuple | None = None  # (pref_key, rid, rel, recording_by_job, score)
    for rid in candidate_ids:
        try:
            rel = mbq.fetch_release(rid)
        except Exception as e:
            log.info("album election: could not fetch release %s: %s", rid, e)
            continue
        recording_by_job: dict[int, str] = {}
        match_scores: list[float] = []
        for job_id, clues in clue_by_job.items():
            score, rec_id = _match_file_to_release(clues, rel)
            if rec_id and score >= _MATCH_FLOOR:
                recording_by_job[job_id] = rec_id
                match_scores.append(score)
        if not recording_by_job:
            continue
        coverage = len(recording_by_job) / len(clue_by_job)
        # Soft coverage factor: full coverage keeps the mean match score
        # untouched; partial coverage discounts it without letting one stray
        # file (a bonus track, a misfiled single) drag an otherwise perfect
        # album below the auto-apply threshold — the stray itself is routed
        # to review separately as ``album_mismatch``.
        group_score = (sum(match_scores) / len(match_scores)) * (0.5 + 0.5 * coverage)
        official = rel.get("status") == "Official"
        matches_library = _library_majority_release(rel)
        total = mbq._release_track_total(rel) or 0
        pref_key = (
            -len(recording_by_job),   # coverage first
            not official,             # Official releases next
            not matches_library,      # then the edition the library already uses
            -total,                   # then the larger edition
            rid,                      # deterministic tail
        )
        if best is None or pref_key < best[0]:
            best = (pref_key, rid, rel, recording_by_job, group_score)

    if best is None:
        return None
    _, rid, rel, recording_by_job, group_score = best
    unmatched = set(clue_by_job) - set(recording_by_job)
    log.info(
        "album election %s: release %s ('%s') covers %d/%d file(s), score=%.3f",
        group_key, rid, rel.get("title"), len(recording_by_job),
        len(clue_by_job), group_score,
    )
    return GroupElection(
        release_id=rid,
        release_doc=rel,
        recording_by_job=recording_by_job,
        score=group_score,
        unmatched_job_ids=unmatched,
        member_job_ids=frozenset(j.id for j in jobs),
    )
