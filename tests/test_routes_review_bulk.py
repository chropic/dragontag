"""/review/bulk-apply applies each job's *chosen* candidate as one background
job, falling back to the stored top candidate when the user left a job's pick
untouched."""
import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from dragontag.app import main as main_mod
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


@pytest.fixture()
def stub_apply(monkeypatch):
    """Record the (recording_id, release_id) each job is committed with and
    mark it done, without touching MusicBrainz or the filesystem."""
    applied: dict[int, tuple[str, str]] = {}

    def fake_assemble_tags(*, release_id, recording_id):
        return types.SimpleNamespace(
            recording_id=recording_id, release_id=release_id,
            cover_bytes=None, cover_mime=None,
            # Fields the shared schema guarantees (pipeline.prepare_tags)
            # read/set before _commit_tag_path.
            release_type=None, release_status=None, track_total=None,
        )

    def fake_commit(s, job, src, tags, *, score):
        applied[job.id] = (tags.recording_id, tags.release_id)
        job.status = JobStatus.done
        s.add(job)
        s.commit()

    monkeypatch.setattr(mbq, "assemble_tags", fake_assemble_tags)
    monkeypatch.setattr(pipeline_mod, "_commit_tag_path", fake_commit)
    return applied


def _mk_review_job(candidates: list[dict]) -> int:
    with session() as s:
        j = Job(
            source_path="/tmp/song.flac", original_name="song.flac", kind="ingest",
            status=JobStatus.needs_review, candidates_json={"items": candidates},
        )
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def _batch_job_id(resp) -> int:
    trig = json.loads(resp.headers["HX-Trigger"])["showToast"]
    return int(trig["job_id"])


def _wait_done(job_id: int, timeout: float = 10.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session() as s:
            j = s.get(Job, job_id)
            if j and j.status in (JobStatus.done, JobStatus.error):
                s.expunge(j)
                return j
        time.sleep(0.05)
    raise AssertionError("batch job did not finish in time")


def test_bulk_apply_respects_pick_and_falls_back_to_top(client, stub_apply):
    cands_a = [
        {"recording_id": "rec-a1", "release_id": "rel-a1", "score": 0.9, "title": "A", "album": "AA"},
        {"recording_id": "rec-a2", "release_id": "rel-a2", "score": 0.7, "title": "A", "album": "AB"},
    ]
    cands_b = [
        {"recording_id": "rec-b1", "release_id": "rel-b1", "score": 0.8, "title": "B", "album": "BB"},
    ]
    job_a = _mk_review_job(cands_a)
    job_b = _mk_review_job(cands_b)

    resp = client.post("/review/bulk-apply", data={
        "job_ids": [str(job_a), str(job_b)],
        # Job A: user chose the *second* (non-top) candidate.
        f"pick_{job_a}": "rec-a2|rel-a2",
        # Job B: no pick submitted → should fall back to its top candidate.
    })
    assert resp.status_code == 303
    _wait_done(_batch_job_id(resp))

    assert stub_apply[job_a] == ("rec-a2", "rel-a2")   # chosen, not top
    assert stub_apply[job_b] == ("rec-b1", "rel-b1")   # fallback to top


def test_bulk_apply_nothing_selected_is_error_toast(client, stub_apply):
    resp = client.post("/review/bulk-apply", data={})
    assert resp.status_code == 303
    trig = json.loads(resp.headers["HX-Trigger"])["showToast"]
    assert trig["level"] == "error"
    assert "select review items" in trig["message"]
    assert not stub_apply


def test_bulk_apply_selected_but_unresolvable_names_the_real_problem(client, stub_apply):
    # Items WERE checked but carry no candidates (no-match / album-mismatch
    # style rows) — the error must not claim nothing was selected.
    job = _mk_review_job([])

    resp = client.post("/review/bulk-apply", data={"job_ids": [str(job)]})

    assert resp.status_code == 303
    trig = json.loads(resp.headers["HX-Trigger"])["showToast"]
    assert trig["level"] == "error"
    assert "select review items" not in trig["message"]
    assert "no pick or stored candidate" in trig["message"].lower() or "candidate" in trig["message"]
    with session() as s:
        assert s.get(Job, job).status == JobStatus.needs_review  # untouched


def test_bulk_apply_mixed_selection_applies_resolvable_and_reports_skips(client, stub_apply):
    good = _mk_review_job([
        {"recording_id": "rec-g", "release_id": "rel-g", "score": 0.9, "title": "G", "album": "GG"},
    ])
    bad = _mk_review_job([])

    resp = client.post("/review/bulk-apply", data={"job_ids": [str(good), str(bad)]})

    assert resp.status_code == 303
    trig = json.loads(resp.headers["HX-Trigger"])["showToast"]
    assert "skipped" in trig["message"]
    _wait_done(_batch_job_id(resp))
    assert stub_apply[good] == ("rec-g", "rel-g")
    with session() as s:
        assert s.get(Job, bad).status == JobStatus.needs_review


def test_bulk_apply_preflips_selected_jobs_out_of_review(client, monkeypatch, stub_apply):
    # Block the chain from running so we can observe the in-route state flip.
    import dragontag.app.tasks as tasks_mod
    monkeypatch.setattr(main_mod.tasks, "run_chain", lambda kind, name, steps: 999)

    job = _mk_review_job([
        {"recording_id": "r", "release_id": "l", "score": 0.9, "title": "T", "album": "A"},
    ])
    resp = client.post("/review/bulk-apply", data={"job_ids": [str(job)]})
    assert resp.status_code == 303
    with session() as s:
        assert s.get(Job, job).status == JobStatus.tagging
