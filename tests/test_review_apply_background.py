"""Single review apply must not run MusicBrainz/cover/lyrics/move work in the
HTTP request thread (it hung the browser). The route pre-flips the job to
`tagging` and queues a `review_apply` background task via the shared
`_apply_review_match` closure."""
import json
import time
import types

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.ingest import pipeline as pipeline_mod
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _mk_review_job(**kw) -> int:
    with session() as s:
        j = Job(source_path="/tmp/one.flac", original_name="one.flac", kind="ingest",
                status=JobStatus.needs_review, **kw)
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def _wait(job_id: int, statuses, timeout: float = 10.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session() as s:
            j = s.get(Job, job_id)
            if j and j.status in statuses:
                s.expunge(j)
                return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {statuses}")


def test_apply_returns_immediately_and_commits_in_background(client, monkeypatch):
    committed = {}

    def fake_assemble_tags(*, release_id, recording_id):
        return types.SimpleNamespace(
            recording_id=recording_id, release_id=release_id,
            cover_bytes=None, cover_mime=None,
            release_type=None, release_status=None, track_total=None,
        )

    def fake_commit(s, job, src, tags, *, score):
        committed[job.id] = (tags.recording_id, tags.release_id, tags.release_type)
        job.status = JobStatus.done
        s.add(job)
        s.commit()

    monkeypatch.setattr(mbq, "assemble_tags", fake_assemble_tags)
    monkeypatch.setattr(pipeline_mod, "_commit_tag_path", fake_commit)

    jid = _mk_review_job()
    resp = client.post(
        f"/review/{jid}/apply",
        data={"pick": "rec-1|rel-1", "release_type_override": "EP"},
    )

    assert resp.status_code == 303
    trig = json.loads(resp.headers["HX-Trigger"])["showToast"]
    assert "job_id" in trig  # clickable background-job toast

    row = _wait(jid, (JobStatus.done,))
    assert committed[jid] == ("rec-1", "rel-1", "EP")  # override survived
    # A tracked review_apply task ran it.
    with session() as s:
        assert s.exec(
            select(Job).where(Job.kind == "review_apply", Job.id == trig["job_id"])
        ).first() is not None


def test_double_submit_is_rejected_while_first_apply_runs(client, monkeypatch):
    # Freeze the background task so the job stays in `tagging`.
    from dragontag.app import main as main_mod
    monkeypatch.setattr(main_mod.tasks, "run_task", lambda kind, name, fn: 4242)

    jid = _mk_review_job()
    first = client.post(f"/review/{jid}/apply", data={"pick": "r|l"})
    assert first.status_code == 303
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.tagging

    second = client.post(f"/review/{jid}/apply", data={"pick": "r|l"})
    trig = json.loads(second.headers["HX-Trigger"])["showToast"]
    assert trig["level"] == "error"
    assert "not awaiting review" in trig["message"]


def test_assemble_failure_returns_job_to_review(client, monkeypatch):
    def boom(*, release_id, recording_id):
        raise RuntimeError("MB is down")

    monkeypatch.setattr(mbq, "assemble_tags", boom)

    jid = _mk_review_job()
    resp = client.post(f"/review/{jid}/apply", data={"pick": "r|l"})
    assert resp.status_code == 303

    row = _wait(jid, (JobStatus.needs_review,))
    assert "assemble_tags" in (row.log or "")
