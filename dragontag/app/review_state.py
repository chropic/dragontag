"""Lifecycle helpers for review drafts and MusicBrainz contributions."""
from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from .models import Job, JobStatus, MusicBrainzContribution, ReviewDraft
from .timeutil import now_utc


MANUAL_FIELDS = {
    "title",
    "artist",
    "album_artist",
    "album",
    "date",
    "track_num",
    "track_total",
    "disc_num",
    "disc_total",
    "release_type",
    "advisory",
    "genres",
}

# These rows have not produced an external outcome and can safely disappear
# with the review card.  Handoff and later states remain as an audit trail.
UNRESOLVED_CONTRIBUTION_STATUSES = {
    "draft", "preflight", "preflight_error", "preflight_cancelled"
}


def normalize_manual_fields(value: Any) -> dict[str, Any]:
    """Return a bounded JSON-safe manual draft payload."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for name in MANUAL_FIELDS:
        raw = value.get(name)
        if name in {"artist", "album_artist"}:
            if not isinstance(raw, list):
                raw = [] if raw in (None, "") else [raw]
            out[name] = [str(item)[:500] for item in raw[:50]]
        elif raw is None:
            out[name] = ""
        else:
            out[name] = str(raw)[:4000]
    return out


def save_review_draft(s: Session, job: Job, fields: Any) -> ReviewDraft:
    if job.status != JobStatus.needs_review:
        raise ValueError("job is not awaiting review")
    draft = s.get(ReviewDraft, job.id)
    now = now_utc()
    if draft is None:
        draft = ReviewDraft(job_id=job.id, created_at=now)
    draft.fields_json = normalize_manual_fields(fields)
    draft.updated_at = now
    s.add(draft)
    return draft


def cleanup_review_state(
    s: Session,
    job_id: int,
    *,
    keep_contribution_id: int | None = None,
) -> None:
    """Remove local draft state while retaining external contribution outcomes."""
    draft = s.get(ReviewDraft, job_id)
    if draft is not None:
        s.delete(draft)
    contributions = s.exec(
        select(MusicBrainzContribution).where(MusicBrainzContribution.job_id == job_id)
    ).all()
    for contribution in contributions:
        if contribution.id == keep_contribution_id:
            continue
        if contribution.status in UNRESOLVED_CONTRIBUTION_STATUSES:
            s.delete(contribution)


def prune_stale_review_state(s: Session) -> tuple[int, int]:
    """Drop drafts/preflights whose job no longer exists in review."""
    live_ids = set(
        s.exec(select(Job.id).where(Job.status == JobStatus.needs_review)).all()
    )
    draft_count = 0
    for draft in s.exec(select(ReviewDraft)).all():
        if draft.job_id not in live_ids:
            s.delete(draft)
            draft_count += 1
    contribution_count = 0
    rows = s.exec(
        select(MusicBrainzContribution).where(
            MusicBrainzContribution.status.in_(UNRESOLVED_CONTRIBUTION_STATUSES)
        )
    ).all()
    for contribution in rows:
        if contribution.job_id not in live_ids:
            s.delete(contribution)
            contribution_count += 1
    return draft_count, contribution_count
