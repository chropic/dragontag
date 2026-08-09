import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import (
    Job,
    JobStatus,
    MusicBrainzContribution,
    ReviewDraft,
    ReviewReason,
)


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def review_job(reason=ReviewReason.no_match):
    with session() as s:
        job = Job(
            source_path="/tmp/draft.flac",
            original_name="draft.flac",
            status=JobStatus.needs_review,
            review_reason=reason,
        )
        s.add(job); s.commit(); s.refresh(job)
        return job.id


def test_draft_route_is_authenticated():
    client = TestClient(app, follow_redirects=False)
    response = client.post("/review/999/draft", json={"fields": {"title": "x"}})
    assert response.status_code == 303


def test_autosave_restore_and_explicit_reset(client):
    job_id = review_job()
    response = client.post(f"/review/{job_id}/draft", json={
        "fields": {"title": "Saved title", "artist": ["A", "B"], "ignored": "no"}
    })
    assert response.status_code == 200
    with session() as s:
        draft = s.get(ReviewDraft, job_id)
        assert draft.fields_json["title"] == "Saved title"
        assert draft.fields_json["artist"] == ["A", "B"]
        assert "ignored" not in draft.fields_json
    page = client.get("/queue")
    assert page.status_code == 200
    assert "Saved title" in page.text
    response = client.post(f"/review/{job_id}/reset")
    assert response.status_code == 303
    with session() as s:
        assert s.get(ReviewDraft, job_id) is None


def test_resolution_cleanup_keeps_external_outcome(client):
    job_id = review_job()
    with session() as s:
        s.add(ReviewDraft(job_id=job_id, fields_json={"title": "draft"}))
        s.add(MusicBrainzContribution(job_id=job_id, mode="release", status="draft"))
        outcome = MusicBrainzContribution(job_id=job_id, mode="release", status="submitted")
        s.add(outcome)
        s.commit(); s.refresh(outcome); outcome_id = outcome.id
    response = client.post(f"/review/{job_id}/skip", headers={"HX-Request": "true"})
    assert response.status_code == 200
    with session() as s:
        assert s.get(ReviewDraft, job_id) is None
        rows = s.exec(select(MusicBrainzContribution).where(
            MusicBrainzContribution.job_id == job_id
        )).all()
        assert [row.status for row in rows] == ["submitted"]
    assert client.post("/jobs/clear-selected", data={"job_ids": job_id}).status_code == 303
    with session() as s:
        assert s.get(Job, job_id) is None
        assert s.get(MusicBrainzContribution, outcome_id).status == "submitted"
    history = client.get("/musicbrainz/contributions")
    assert history.status_code == 200
    assert f"#{outcome_id}" in history.text
    assert client.get(f"/musicbrainz/contributions/{outcome_id}").status_code == 200


def test_stale_cleanup_is_explicit_and_not_based_on_dom_presence(client):
    job_id = review_job()
    with session() as s:
        s.add(ReviewDraft(job_id=job_id, fields_json={"title": "stale"}))
        s.commit()
        job = s.get(Job, job_id)
        job.status = JobStatus.error
        s.add(job); s.commit()
    with session() as s:
        from dragontag.app.review_state import prune_stale_review_state
        assert prune_stale_review_state(s) == (1, 0)
        s.commit()
        assert s.get(ReviewDraft, job_id) is None
