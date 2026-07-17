"""The retag route must return immediately (the folder walk + per-file enqueue
used to run in the HTTP request thread and hang the browser on large folders);
the enqueue now runs as a background 'retag' task that _batch_guard ignores."""
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.main import _batch_guard, app, require_auth
from dragontag.app.models import Job, JobStatus


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 50)


def _wait_for_job(job_id: int, timeout: float = 10.0) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session() as s:
            row = s.get(Job, job_id)
            if row and row.status not in (JobStatus.running, JobStatus.queued):
                return row
        time.sleep(0.05)
    raise AssertionError("retag job did not finish in time")


def test_retag_returns_immediately_and_enqueues_in_background(client, tmp_path):
    for n in ("01.wav", "02.wav"):
        _make_wav(tmp_path / "Album" / n)

    resp = client.post("/library/bulk-retag", data={"source_path": str(tmp_path), "dry_run": "on"})

    assert resp.status_code == 303
    with session() as s:
        retag_job = s.exec(
            select(Job).where(Job.kind == "retag").order_by(Job.id.desc())
        ).first()
    assert retag_job is not None
    done = _wait_for_job(retag_job.id)
    assert done.status == JobStatus.done, done.error
    # The background task actually created the per-file ingest jobs.
    with session() as s:
        ingest = s.exec(
            select(Job).where(
                Job.kind == "ingest",
                Job.source_path.in_([str(tmp_path / "Album" / n) for n in ("01.wav", "02.wav")]),
            )
        ).all()
    assert len(ingest) == 2
    # Album grouping metadata was applied at enqueue time.
    assert all(j.group_key == str((tmp_path / "Album").resolve()) for j in ingest)


def test_invalid_path_errors_without_creating_a_job(client, tmp_path):
    with session() as s:
        before = len(s.exec(select(Job).where(Job.kind == "retag")).all())

    resp = client.post("/library/bulk-retag", data={"source_path": str(tmp_path / "nope")})

    assert resp.status_code == 204  # htmx-less toast path returns no redirect on error
    assert "error" in resp.headers.get("HX-Trigger", "")
    with session() as s:
        after = len(s.exec(select(Job).where(Job.kind == "retag")).all())
    assert after == before


def test_htmx_request_gets_toast_without_redirect(client, tmp_path):
    _make_wav(tmp_path / "one.wav")
    resp = client.post(
        "/library/bulk-retag",
        data={"source_path": str(tmp_path), "dry_run": "on"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 204
    assert "showToast" in resp.headers.get("HX-Trigger", "")


def test_batch_guard_ignores_running_retag():
    with session() as s:
        row = Job(source_path="x", original_name="x", kind="retag", status=JobStatus.running)
        s.add(row)
        s.commit()
        s.refresh(row)
        rid = row.id
    try:
        assert _batch_guard() is None
    finally:
        with session() as s:
            s.delete(s.get(Job, rid))
            s.commit()
