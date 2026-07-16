"""Per-file pipeline: identify → tag → move.

State for each in-flight file lives in a ``Job`` row (see ``models.py``).
The pipeline runs in a single background worker thread fed by an in-memory
``queue.Queue``; this is plenty for a single-user app and avoids dragging
in Celery/RQ. Jobs that survive a restart are picked up by
:func:`resubmit_pending` on startup.

Two key control-flow branches:

* **Auto-apply path** — score ≥ threshold, MB had a primary-type. Tags are
  written, file is moved, status → ``done``.
* **Review path** — anything else (low score, no MB match, missing
  RELEASETYPE, destination conflict). Status → ``needs_review`` with a
  ``ReviewReason``; the UI presents the right buttons for that reason.
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
from pathlib import Path

import requests
from sqlmodel import Session, select

from ..config import env, settings
from ..db import session
from ..identify import acoustid as acid
from ..identify import existing_tags, filename_parse
from ..identify import musicbrainz as mbq
from ..identify.scoring import score_candidate
from ..library import filelock
from ..library.mover import move, move_lyric_sidecar, write_cover_jpg
from ..library.paths import DestinationUnresolved, build_destination
from ..models import FileChange, Job, JobStatus, ReviewReason, append_job_log
from ..tagging import snapshot
from ..tagging.coverart import fetch_for_release, fetch_for_release_group
from ..tagging.schema import TrackTags
from ..tagging.writers import write_tags
from ..timeutil import now_utc

log = logging.getLogger(__name__)

# Anything outside this whitelist is ignored by the watcher and rejected at
# write time. Kept in lock-step with the dispatch table in
# ``tagging/writers/__init__.py``.
SUPPORTED_EXTS = {".flac", ".mp3", ".wav", ".m4a", ".mp4"}


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------


_ACTIVE_STATUSES = [
    JobStatus.queued,
    JobStatus.identifying,
    JobStatus.tagging,
    JobStatus.moving,
    # A job sitting in needs_review still "owns" its source file (the user
    # hasn't resolved it yet); without this a re-touch of the same path (e.g.
    # the watcher firing again on a slow rewrite) spawns a second job that
    # races the pending review for the same physical file.
    JobStatus.needs_review,
]

# Statuses ``process()`` is actually willing to run. Guards against a job
# being submitted twice for the same id (or submitted after it's already
# moved on to needs_review/done/error) and re-running the pipeline on a file
# another thread/job may already be touching.
_PROCESSABLE_STATUSES = {
    JobStatus.queued,
    JobStatus.identifying,
    JobStatus.tagging,
    JobStatus.moving,
}

# Serializes the dedup check-then-insert in ``enqueue`` so the watcher thread
# and an HTTP/bulk thread can't both miss the existing-job check and create two
# jobs for the same path.
_enqueue_lock = threading.Lock()


def enqueue(
    path: Path,
    *,
    dry_run: bool | None = None,
    requeue_reviews: bool = False,
    group_key: str | None = None,
) -> Job:
    """Persist a new ``Job`` and return it. Doesn't submit to the worker —
    callers call :func:`submit` after committing so the worker can see the row.

    ``dry_run`` is a per-job override: ``None`` follows the global
    ``settings().dry_run``; ``True``/``False`` is an explicit choice for this
    job only (the Library page checkboxes) and never touches the global flag.

    Deduplicates by source path: if an active job already exists for this path
    it is returned as-is, preventing double-processing when the watcher fires
    on a file that was just saved by the upload handler.

    ``requeue_reviews`` controls what a dedup hit on a ``needs_review`` job
    means. Explicit re-tag callers (bulk/batch) pass ``True`` so the stuck job
    is reset to ``queued`` and actually reprocessed — otherwise it would be
    counted as "queued" but silently skipped by ``process()``. The watcher and
    upload paths keep the default ``False``: a re-fired filesystem event must
    not discard a review the user hasn't resolved yet.

    ``group_key`` marks this job as part of an album group (see
    ``ingest/album.py``): jobs sharing a key are identified against ONE
    elected MusicBrainz release so their release-level tags can't drift
    apart. ``None`` keeps the per-track path.
    """
    with _enqueue_lock, session() as s:
        existing = s.exec(
            select(Job).where(
                Job.source_path == str(path),
                Job.status.in_(_ACTIVE_STATUSES),
            )
        ).first()
        if existing:
            if requeue_reviews and existing.status == JobStatus.needs_review:
                _set(
                    existing,
                    status=JobStatus.queued,
                    review_reason=None,
                    error=None,
                    dry_run_override=dry_run,
                    group_key=group_key,
                )
                s.add(existing)
                s.commit()
                s.refresh(existing)
                if group_key:
                    from . import album as album_grouping
                    album_grouping.invalidate(group_key)
            return existing
        job = Job(
            source_path=str(path),
            original_name=path.name,
            status=JobStatus.queued,
            dry_run_override=dry_run,
            group_key=group_key,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


# ---------------------------------------------------------------------------
# Pipeline mechanics
# ---------------------------------------------------------------------------


# Candidates whose score is within this margin of the top score are treated
# as equally plausible identifications, and the release among them is chosen
# by _release_pref_key instead of raw score order. Without this, near-tied
# editions of one album (scores differing by noise in the title/duration
# similarity) get picked per-track, scattering an album's tracks across
# several MUSICBRAINZ_ALBUMIDs — the main cause of album splitting.
_CONSENSUS_EPSILON = 0.05


def _existing_release_for_group(rg_id: str | None) -> str | None:
    """The library's majority ``mb_album_id`` for a release group, or None.

    Lets a new track of an album we already hold land on the same release its
    siblings used, instead of whichever edition scored a hair higher today.
    """
    if not rg_id:
        return None
    from collections import Counter
    from ..models import Track
    with session() as s:
        rows = s.exec(
            select(Track.mb_album_id).where(
                Track.mb_release_group_id == rg_id,
                Track.mb_album_id.is_not(None),
            )
        ).all()
    if not rows:
        return None
    counts = Counter(rows)
    best = max(counts.values())
    # Deterministic on ties so two concurrent ingests can't flip-flop.
    return sorted(rid for rid, c in counts.items() if c == best)[0]


def _select_candidate(scored: list[tuple]) -> tuple:
    """Pick the (total, breakdown, candidate) entry to auto-apply.

    ``scored`` is sorted best-first. Among the near-tied head of the list
    (within ``_CONSENSUS_EPSILON``), prefer — in order — an Official release,
    the release the library already uses for this release group, the edition
    with the most tracks, then the lexicographically smallest release id.
    """
    top_total = scored[0][0]
    near = [e for e in scored if top_total - e[0] <= _CONSENSUS_EPSILON]
    if len(near) == 1:
        return scored[0]

    existing_cache: dict[str, str | None] = {}

    def existing_for(rg_id: str | None) -> str | None:
        if not rg_id:
            return None
        if rg_id not in existing_cache:
            existing_cache[rg_id] = _existing_release_for_group(rg_id)
        return existing_cache[rg_id]

    def pref(entry: tuple):
        _, _, c = entry
        rel = c.raw_release or {}
        official = rel.get("status") == "Official"
        rg_id = (rel.get("release-group") or {}).get("id")
        matches_library = c.release_id == existing_for(rg_id)
        try:
            track_count = int(rel.get("track-count") or 0)
        except (TypeError, ValueError):
            track_count = 0
        return (not official, not matches_library, -track_count, c.release_id)

    return min(near, key=pref)


def _infer_release_type(track_total: int | None) -> str:
    """Derive RELEASETYPE from track count when MB omits the primary-type."""
    if track_total is None or track_total >= 7:
        return "Album"
    if track_total == 1:
        return "Single"
    return "EP"


def _set(job: Job, **kwargs) -> None:
    """Mutate ``job`` in-place and bump ``updated_at``."""
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.updated_at = now_utc()


def _append_log(job: Job, line: str) -> None:
    """Append a human-readable progress line to the job's log column."""
    job.log = append_job_log(job.log, line.rstrip() + "\n")


def process(job_id: int) -> None:
    """Top-level worker entry point: load the job, run the pipeline, save errors."""
    with session() as s:
        job = s.get(Job, job_id)
        if not job or job.status not in _PROCESSABLE_STATUSES:
            return
        try:
            _process_inner(s, job)
        except Exception as e:
            # Catch-all: the worker thread must keep running even if one job
            # blows up. The full traceback is preserved on the job row so the
            # UI can surface it.
            log.exception("pipeline failed")
            _set(job, status=JobStatus.error, error=f"{e}\n{traceback.format_exc()}")
            s.add(job)
            s.commit()
            from ..notify import post_error
            post_error(job)


def _process_inner(s: Session, job: Job) -> None:
    src = Path(job.source_path)
    if not src.exists():
        # Requeued jobs have their source moved to the library; fall back to
        # destination_path so the pipeline can re-tag the file in place.
        if job.destination_path and Path(job.destination_path).exists():
            src = Path(job.destination_path)
            job.source_path = str(src)
        else:
            _set(job, status=JobStatus.error, error="Source file not found")
            s.add(job)
            s.commit()
            return

    _set(job, status=JobStatus.identifying)
    _append_log(job, f"Identifying {src.name}")
    s.add(job)
    s.commit()

    # ----- gather clues from the file itself -----
    existing = existing_tags.read(src)
    fname = filename_parse.parse(src)
    clues = {
        "title": existing.get("title") or fname.get("title"),
        "artist": existing.get("artist") or fname.get("artist"),
        "album": existing.get("album"),
        "duration": existing.get("duration"),
    }
    # Commit (not flush) before the network-bound identify calls below: a
    # flush issues the UPDATE and takes SQLite's write lock, and holding it
    # open across MusicBrainz/AcoustID retries blocks every other writer
    # (watcher enqueues, any POST from the UI) with "database is locked".
    _append_log(job, f"Clues: {clues}")
    s.add(job)
    s.commit()

    # ----- step 0: album-group election -----
    # Files enqueued together from one album folder are identified as a unit:
    # one release is elected for the whole group (ingest/album.py) and every
    # member is assembled from that single release document, so release-level
    # tags cannot scatter across editions. This branch comes BEFORE the MBID
    # short-circuit on purpose — a per-file album id that disagrees with its
    # siblings is exactly the drift that split albums, so inside a group it is
    # demoted to a strongly-weighted election candidate.
    if job.group_key:
        from . import album as album_grouping

        election = None
        try:
            election = album_grouping.get_or_elect(job.group_key)
        except Exception:
            log.exception("album election failed for %s", job.group_key)
        if election is not None:
            if job.id in election.recording_by_job:
                job.candidates_json = {
                    "items": [
                        {
                            "recording_id": election.recording_by_job[job.id],
                            "release_id": election.release_id,
                            "score": election.score,
                            "title": None,
                            "album": election.release_doc.get("title"),
                        }
                    ]
                }
                _append_log(
                    job,
                    f"Album group elected release {election.release_id} "
                    f"('{election.release_doc.get('title')}') score={election.score:.3f}",
                )
                if election.score >= settings().score_threshold:
                    try:
                        tags = mbq.assemble_tags(
                            release_id=election.release_id,
                            recording_id=election.recording_by_job[job.id],
                            rel=election.release_doc,
                        )
                    except Exception as e:
                        _append_log(job, f"group assemble_tags failed: {e}")
                    else:
                        _finalize_and_commit(s, job, src, tags, score=election.score)
                        return
                else:
                    # Whole group goes to review with the elected candidate
                    # first in candidates_json, so one review click can apply
                    # the group's consensus.
                    _append_log(job, "Group score below threshold — review")
                    _set(
                        job,
                        status=JobStatus.needs_review,
                        review_reason=ReviewReason.low_score,
                        score=election.score,
                    )
                    s.add(job)
                    s.commit()
                    return
            else:
                # The rest of the folder matched a release this file isn't on
                # (compilation stray, edition-exclusive bonus track…). Never
                # silently force it onto the album — surface it for review.
                _append_log(
                    job,
                    f"Not on the group's elected release {election.release_id} "
                    f"('{election.release_doc.get('title')}')",
                )
                _set(
                    job,
                    status=JobStatus.needs_review,
                    review_reason=ReviewReason.album_mismatch,
                )
                s.add(job)
                s.commit()
                return
        # No election possible (MB down / zero candidates) — fall through to
        # the per-track path below.
        _append_log(job, "Album group election unavailable — per-track fallback")

    # ----- step 1: short-circuit on existing MBIDs -----
    # If the file was already tagged by Picard (or by us), the MB IDs are the
    # most reliable identifier we can have — skip the search entirely. The
    # finalize step (not a direct _commit_tag_path call) keeps this path under
    # the same dry-run gate and RELEASETYPE/formatting rules as the search
    # path — a dry-run bulk re-tag of an already-tagged library must stay a
    # preview here too.
    if existing.get("mb_track_id") and existing.get("mb_album_id"):
        try:
            tags = mbq.assemble_tags(
                release_id=existing["mb_album_id"],
                recording_id=existing["mb_track_id"],
            )
        except Exception as e:
            # Pre-existing MBIDs occasionally point at deleted/redirected MB
            # entries. Fall through to the regular search path.
            _append_log(job, f"MBID short-circuit failed: {e}")
        else:
            _finalize_and_commit(s, job, src, tags, score=1.0)
            return

    # ----- step 2: MB text search -----
    cands = mbq.search_candidates(
        title=clues.get("title"),
        artist=clues.get("artist"),
        album=clues.get("album"),
        duration_sec=clues.get("duration"),
        limit=5,
    )

    # ----- step 3: AcoustID fallback when text search came up empty -----
    if (not cands) and settings().acoustid_enabled:
        _append_log(job, "Falling back to AcoustID fingerprint")
        for m in acid.lookup(src)[:3]:
            if not m.recording_id:
                continue
            try:
                rec = mbq.fetch_recording(m.recording_id)
            except Exception:
                continue
            # AcoustID gives us a recording; we still need a release to
            # form a Candidate. Expand into the first few releases.
            for rel in (rec.get("release-list") or [])[:3]:
                cands.append(
                    mbq.Candidate(
                        score=m.score,
                        recording_id=m.recording_id,
                        release_id=rel["id"],
                        acoustid_id=m.acoustid_id,
                        raw_recording=rec,
                        raw_release=rel,
                    )
                )

    if not cands:
        _set(job, status=JobStatus.needs_review, review_reason=ReviewReason.no_match)
        _append_log(job, "No candidates found")
        s.add(job)
        s.commit()
        return

    # ----- step 4: rank candidates -----
    scored = []
    for c in cands:
        sb = score_candidate(
            candidate_recording=c.raw_recording,
            candidate_release=c.raw_release,
            clues=clues,
            mb_search_score=c.score,
        )
        scored.append((sb.total, sb, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Persist the top 5 so the review UI doesn't need to re-query MB.
    job.candidates_json = {
        "items": [
            {
                "recording_id": c.recording_id,
                "release_id": c.release_id,
                "score": t,
                "title": c.raw_recording.get("title"),
                "album": c.raw_release.get("title"),
            }
            for (t, _, c) in scored[:5]
        ]
    }
    best_total, _, best = _select_candidate(scored)
    _append_log(job, f"Best candidate score={best_total:.3f}")
    if best is not scored[0][2]:
        _append_log(
            job,
            f"Consensus pick: release {best.release_id} "
            f"('{best.raw_release.get('title')}') over score-leader "
            f"{scored[0][2].release_id} (Δ={scored[0][0] - best_total:.3f})",
        )

    # ----- step 5: branch on threshold -----
    # Gate on the raw score leader: the consensus pick may sit up to
    # _CONSENSUS_EPSILON below it, and preferring a consistent release must
    # not push an otherwise auto-applying job into review.
    threshold = settings().score_threshold
    if scored[0][0] < threshold:
        _set(
            job,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.low_score,
            score=best_total,
        )
        s.add(job)
        s.commit()
        return

    # ----- step 6: fully resolve the chosen candidate -----
    try:
        tags = mbq.assemble_tags(release_id=best.release_id, recording_id=best.recording_id)
        if best.acoustid_id:
            tags.acoustid_id = best.acoustid_id
    except Exception as e:
        _append_log(job, f"assemble_tags failed: {e}")
        _set(
            job,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.no_match,
            score=best_total,
        )
        s.add(job)
        s.commit()
        return

    _finalize_and_commit(s, job, src, tags, score=best_total)


def prepare_tags(job: Job | None, tags: TrackTags) -> None:
    """Apply the schema guarantees every write path must honor.

    Optional smart formatting, RELEASETYPE inference (the only mandatory
    field) and the RELEASESTATUS default. Shared by ``_finalize_and_commit``
    and the review/apply routes in ``main.py`` — those call
    ``_commit_tag_path`` directly (an explicit user commit must not re-enter
    the dry-run gate), so without this hook their files would skip the
    formatting pass and could be written with no RELEASETYPE at all whenever
    MB lacks a release-group primary-type.
    """
    # ----- optional smart formatting -----
    cfg = settings()
    if cfg.format_title_case or cfg.format_fix_qualifiers or cfg.format_grammar_correct:
        from ..tagging.formatter import apply as _fmt
        kw = dict(
            title_case=cfg.format_title_case,
            fix_quals=cfg.format_fix_qualifiers,
            grammar=cfg.format_grammar_correct,
            grammar_allcaps=cfg.format_grammar_fix_allcaps,
            grammar_contractions=cfg.format_grammar_fix_contractions,
            grammar_possessives=cfg.format_grammar_fix_possessives,
            grammar_punct_spacing=cfg.format_grammar_fix_punct_spacing,
        )
        tags.title = _fmt(tags.title, **kw)
        tags.album = _fmt(tags.album, **kw)
        tags.artist_display = _fmt(tags.artist_display, **kw)
        tags.album_artist_display = _fmt(tags.album_artist_display, **kw)
        tags.composers = [_fmt(c, **kw) or c for c in tags.composers]

    # RELEASETYPE is the only field we treat as mandatory — the user's
    # convention demands one, and getting it wrong (e.g. tagging a single as
    # an album) corrupts a lot of downstream tooling. ``_infer_release_type``
    # always yields a value from the track count, so this never blocks.
    if not tags.release_type:
        # Prefer the release-wide count: track_total is per-disc, and a small
        # final disc must not demote its tracks to "EP" while disc 1 says
        # "Album" — that difference alone splits the album in players.
        # getattr: review/apply paths may pass duck-typed tag objects that
        # predate this field (stubs, stored candidate shims).
        total = getattr(tags, "release_track_total", None) or tags.track_total
        tags.release_type = _infer_release_type(total)
        if job is not None:
            _append_log(job, f"RELEASETYPE inferred as '{tags.release_type}' from track count={total}")

    if not tags.release_status:
        tags.release_status = "Official"


def _finalize_and_commit(s: Session, job: Job, src: Path, tags: TrackTags, *, score: float) -> None:
    """Shared tail of every identification path (MB search, MBID short-circuit).

    Applies the shared schema guarantees (``prepare_tags``) and routes through
    the dry-run gate before anything destructive happens — the gate must sit
    here rather than in the search path only, or a dry-run over an
    already-tagged library (where every file short-circuits on its MBIDs)
    would silently write.
    """
    prepare_tags(job, tags)

    effective_dry_run = (
        job.dry_run_override if job.dry_run_override is not None else settings().dry_run
    )
    if effective_dry_run:
        lib_root = _pick_library_folder()
        try:
            dest = build_destination(tags, src.suffix, library_root=lib_root)
        except DestinationUnresolved as e:
            _append_log(job, f"Destination unresolved (library scan failed): {e}")
            job.chosen_tags_json = _tags_to_dict(tags)
            _set(
                job,
                score=score,
                status=JobStatus.needs_review,
                review_reason=ReviewReason.destination_unresolved,
            )
            s.add(job)
            s.commit()
            return
        job.chosen_tags_json = _tags_to_dict(tags)
        _set(
            job,
            destination_path=str(dest),
            score=score,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.dry_run,
        )
        s.add(job)
        s.commit()
        return

    _commit_tag_path(s, job, src, tags, score=score)


def _commit_tag_path(s: Session, job: Job, src: Path, tags: TrackTags, *, score: float) -> None:
    """Final 'happy path' actions: cover art + write + move.

    Also reachable from the review UI's apply handler (after the user picks a
    candidate or overrides RELEASETYPE), so it must be safe to call with a
    pre-assembled ``tags`` object.
    """

    # ----- cover art (best resolution available) -----
    # Skip the CAA fetch when the caller already supplied cover bytes
    # (e.g. the review UI cover-art picker or a custom upload).
    cover = None
    if not tags.cover_bytes:
        # A CAA *fetch failure* (5xx/SSL/connection) is transient and retriable,
        # so it must not abort the whole job (previously it crashed the pipeline
        # and left the file untagged). Route to review instead — bailing here,
        # before any destructive write/move, keeps ``source_path`` valid so the
        # review "Apply" path can re-run this fetch later. A genuine "no art in
        # CAA" (HTTP 404) returns None and is *not* an error: the job proceeds
        # art-less as before.
        try:
            cover = fetch_for_release(tags.mb_album_id) if tags.mb_album_id else None
            # The release-group cover is shared across every edition in the group,
            # so it can bleed one album's art onto another. Only use it when the
            # user has explicitly opted in.
            if (
                not cover
                and tags.mb_release_group_id
                and settings().cover_allow_release_group_fallback
            ):
                cover = fetch_for_release_group(tags.mb_release_group_id)
        except requests.RequestException as e:
            _append_log(job, f"Cover art fetch failed ({e}); routing to review")
            _set(
                job,
                status=JobStatus.needs_review,
                review_reason=ReviewReason.cover_fetch_failed,
                score=score,
            )
            s.add(job)
            s.commit()
            return
        if cover:
            tags.cover_bytes = cover.data
            tags.cover_mime = cover.mime
            _append_log(job, f"Fetched cover {cover.width}x{cover.height} ({cover.mime})")

    # ----- lyrics + advisory -----
    if settings().lyrics_enabled:
        from ..tagging import lyrics_fetcher
        from ..tagging.advisory import is_explicit
        fetched = lyrics_fetcher.fetch(
            artist=tags.artist_display,
            title=tags.title,
            album=tags.album,
        )
        if fetched is not None:
            tags.lyrics = fetched
            tags.advisory = 1 if is_explicit(fetched) else 0
            rating = "explicit" if tags.advisory else "clean"
            _append_log(job, f"Lyrics fetched ({rating})")
        else:
            _append_log(job, "No lyrics found")

    _set(job, status=JobStatus.tagging, score=score)
    job.chosen_tags_json = _tags_to_dict(tags)
    s.add(job)
    s.commit()

    # ----- snapshot original tags before the destructive write (for revert) -----
    # Held for the full write+move so a concurrent revert/move-back (HTTP
    # thread) can't read/rewrite the same file mid-flight (S2).
    with filelock.path_lock(src):
        original_snapshot = snapshot.capture(src)
        original_path = str(src)

        # ----- write tags -----
        try:
            write_tags(src, tags)
        except Exception as e:
            _append_log(job, f"write_tags failed: {e}")
            _set(job, status=JobStatus.error, error=str(e))
            s.add(job)
            s.commit()
            return

        # ----- move into library -----
        lib_root = _pick_library_folder()
        try:
            dest = build_destination(tags, src.suffix, library_root=lib_root, ensure_dirs=True)
        except DestinationUnresolved as e:
            # Moving anyway could mint a case-variant twin directory (the
            # library-nuking failure mode on network shares) — leave the file
            # where it is and let the user retry from review. The in-place tag
            # write above is just as destructive as the happy path's, so it
            # must stay auditable/revertible.
            _append_log(job, f"Destination unresolved (library scan failed): {e}")
            _set(
                job,
                status=JobStatus.needs_review,
                review_reason=ReviewReason.destination_unresolved,
            )
            s.add(job)
            s.commit()
            _record_change(
                s,
                job,
                original_path=original_path,
                original_snapshot=original_snapshot,
                dest=src,
                new_tags=job.chosen_tags_json,
                cover_jpg_created=False,
            )
            return
        # Persist the destination *before* the physical move. If the worker is hard
        # killed (OOM/SIGKILL) mid-move, the job row already records where the file
        # is headed, so crash recovery in ``_process_inner`` (which falls back to
        # ``destination_path`` when the source is gone) can find the moved file and
        # re-tag it in place instead of erroring "Source file not found" and leaving
        # an orphaned, unindexed file in the library.
        _set(job, status=JobStatus.moving, destination_path=str(dest))
        s.add(job)
        s.commit()

        result = move(src, dest, overwrite=False)
        if not result.moved and result.conflict:
            # Don't auto-overwrite — kick to review so the user decides.
            _append_log(job, f"Destination conflict: {dest}")
            _set(
                job,
                status=JobStatus.needs_review,
                review_reason=ReviewReason.destination_conflict,
                destination_path=str(dest),
            )
            s.add(job)
            s.commit()
            # The tags were already rewritten in place above — that write is
            # just as destructive as the happy path's, so it must be auditable
            # and revertible even though the move was blocked. file_path is
            # ``src`` (where the file actually still lives); resolve_conflict
            # re-points the row when the user later moves the file.
            _record_change(
                s,
                job,
                original_path=original_path,
                original_snapshot=original_snapshot,
                dest=src,
                new_tags=job.chosen_tags_json,
                cover_jpg_created=False,
            )
            return

        move_lyric_sidecar(src, dest)

    # ----- side-effect: write cover.jpg next to the file -----
    cover_jpg = dest.parent / "cover.jpg"
    cover_existed = cover_jpg.exists()
    if cover:
        write_cover_jpg(
            dest.parent,
            cover.data,
            min_overwrite_pixels=settings().cover_min_overwrite_pixels,
            new_width=cover.width,
        )
    elif tags.cover_bytes:
        # User-supplied art (picker or custom upload): always write sidecar.
        write_cover_jpg(dest.parent, tags.cover_bytes, min_overwrite_pixels=0, new_width=0)

    track = _upsert_track(s, dest, tags, lib_root, original_path=original_path)
    job.track_id = track.id
    _set(job, status=JobStatus.done, destination_path=str(dest))
    _append_log(job, f"Done -> {dest}")
    s.add(job)
    s.commit()

    # ----- record the change so it can be reviewed / reverted -----
    _record_change(
        s,
        job,
        original_path=original_path,
        original_snapshot=original_snapshot,
        dest=dest,
        new_tags=job.chosen_tags_json,
        cover_jpg_created=(not cover_existed and cover_jpg.exists()),
    )

    from ..notify import post_done
    post_done(job, tags)


def _record_change(
    s: Session,
    job: Job,
    *,
    original_path: str,
    original_snapshot: dict,
    dest: Path,
    new_tags: dict,
    cover_jpg_created: bool,
) -> None:
    """Persist a FileChange audit row, then prune to the most recent rows."""
    change = FileChange(
        job_id=job.id,
        file_path=str(dest),
        original_path=original_path,
        original_name=job.original_name,
        original_tags_json=original_snapshot or {},
        new_tags_json=new_tags or {},
        cover_jpg_created=cover_jpg_created,
    )
    s.add(change)
    s.commit()

    # 0 = unlimited (same convention as genre_limit).
    cap = settings().max_recent_changes
    if cap <= 0:
        return
    stale = s.exec(
        select(FileChange.id).order_by(FileChange.id.desc()).offset(cap)
    ).all()
    if stale:
        for cid in stale:
            obj = s.get(FileChange, cid)
            if obj:
                s.delete(obj)
        s.commit()


def _pick_library_folder() -> Path:
    """Return the path of the first enabled LibraryFolder (by priority, then id).

    Falls back to env().library_path if the table is somehow empty — this
    should not happen after the DB seed in db.py, but guards against it.
    """
    from ..models import LibraryFolder
    with session() as s:
        folder = s.exec(
            select(LibraryFolder)
            .where(LibraryFolder.enabled == True)  # noqa: E712
            .order_by(LibraryFolder.priority, LibraryFolder.id)
        ).first()
    return Path(folder.path) if folder else env().library_path


def _upsert_track(
    s: Session, dest: Path, tags: TrackTags, lib_root: Path, original_path: str | None = None
) -> "Track":
    """Create or update the Track row for a successfully moved file.

    ``original_path`` is the file's pre-move location. When a re-tagged
    in-library file moves to a new canonical destination, the row indexed at
    the old path must be re-pointed rather than left behind — otherwise the
    library lists a phantom entry (and double-counts) until the next scan's
    prune, and the old row's ``protected`` flag is lost to a fresh insert.
    """
    from ..models import LibraryFolder, Track

    folder_row = s.exec(
        select(LibraryFolder).where(LibraryFolder.path == str(lib_root))
    ).first()
    folder_id = folder_row.id if folder_row else None

    now = now_utc()
    duration = existing_tags.read(dest).get("duration")
    existing = s.exec(select(Track).where(Track.path == str(dest))).first()
    if existing is None and original_path and original_path != str(dest):
        existing = s.exec(select(Track).where(Track.path == original_path)).first()
        if existing:
            existing.path = str(dest)
    if existing:
        existing.library_folder_id = folder_id
        existing.title = tags.title
        existing.artist = tags.artist_display
        existing.album = tags.album
        existing.album_artist = tags.album_artist_display
        existing.track_num = tags.track
        existing.track_total = tags.track_total
        existing.disc_num = tags.disc
        existing.disc_total = tags.disc_total
        existing.mb_track_id = tags.mb_track_id
        existing.mb_album_id = tags.mb_album_id
        existing.mb_release_group_id = tags.mb_release_group_id
        existing.mb_album_artist_id = (
            tags.mb_album_artist_ids[0] if tags.mb_album_artist_ids else None
        )
        existing.advisory = tags.advisory
        existing.has_lyrics = bool(tags.lyrics)
        existing.duration = duration
        existing.last_seen = now
        s.add(existing)
        s.commit()
        s.refresh(existing)
        return existing

    track = Track(
        path=str(dest),
        library_folder_id=folder_id,
        title=tags.title,
        artist=tags.artist_display,
        album=tags.album,
        album_artist=tags.album_artist_display,
        track_num=tags.track,
        track_total=tags.track_total,
        disc_num=tags.disc,
        disc_total=tags.disc_total,
        mb_track_id=tags.mb_track_id,
        mb_album_id=tags.mb_album_id,
        mb_release_group_id=tags.mb_release_group_id,
        mb_album_artist_id=(
            tags.mb_album_artist_ids[0] if tags.mb_album_artist_ids else None
        ),
        advisory=tags.advisory,
        has_lyrics=bool(tags.lyrics),
        duration=duration,
        indexed_at=now,
        last_seen=now,
    )
    s.add(track)
    s.commit()
    s.refresh(track)
    return track


def _tags_to_dict(tags) -> dict:
    """JSON-safe view of a ``TrackTags`` for storage on the job row.

    We drop ``cover_bytes`` because it's a binary blob (and large) — the
    cover is embedded in the file, not stored in the DB.
    """
    return {k: v for k, v in tags.__dict__.items() if k != "cover_bytes"}


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

_q: "queue.Queue[int]" = queue.Queue()
_worker_started = False


def _worker_loop() -> None:
    """Pull job IDs off the queue and process them serially.

    Single-threaded by design: MB rate-limits to 1 req/sec, so parallelism
    wouldn't help, and serialization keeps the SQLite write traffic simple.
    """
    while True:
        job_id = _q.get()
        try:
            process(job_id)
        except Exception:
            log.exception("worker error")
        finally:
            _q.task_done()


def start_worker() -> None:
    """Idempotently start the worker thread. Called from FastAPI's startup hook."""
    global _worker_started
    if _worker_started:
        return
    t = threading.Thread(target=_worker_loop, name="dragontag-pipeline", daemon=True)
    t.start()
    _worker_started = True


def submit(job_id: int) -> None:
    """Enqueue a job for the worker (and ensure the worker is running)."""
    start_worker()
    _q.put(job_id)


def resubmit_pending() -> None:
    """Re-queue jobs that were mid-flight at last shutdown.

    Anything in queued/identifying/tagging/moving is safe to restart from
    scratch because the pipeline is idempotent until the move step (the
    final move is the only destructive operation, and ``needs_review`` /
    ``done`` jobs are skipped).
    """
    from ..models import ACTIVE_JOB_STATUSES
    with session() as s:
        rows = s.exec(
            select(Job).where(Job.status.in_(list(ACTIVE_JOB_STATUSES)))
        ).all()
        # Non-ingest tasks (scans, organizes, …) don't carry enough state to
        # resume — mark them failed instead of feeding them to the pipeline.
        resubmit_ids: list[int] = []
        for j in rows:
            if j.kind != "ingest" or j.status == JobStatus.running:
                j.status = JobStatus.error
                j.error = "interrupted by restart"
                s.add(j)
            else:
                resubmit_ids.append(j.id)
        s.commit()
    for jid in resubmit_ids:
        submit(jid)
