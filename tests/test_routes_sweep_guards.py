"""Route-level guards from the bug sweep: re-applying a resolved review job,
out-of-range settings values, and Run-Now racing a same-kind running task must
all come back as toasts — never a 500 or a done→error flip.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus, ScheduledTask


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_review_apply_on_done_job_is_error_toast(client, tmp_path):
    p = tmp_path / "song.flac"
    p.write_bytes(b"\x00")
    with session() as s:
        job = Job(source_path=str(p), original_name=p.name, status=JobStatus.done)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    resp = client.post(f"/review/{job_id}/apply", data={"pick": "rec|rel"})

    assert resp.status_code == 303
    assert "error" in resp.headers["HX-Trigger"]
    with session() as s:
        row = s.get(Job, job_id)
        assert row.status == JobStatus.done  # not flipped to error
        s.delete(row)
        s.commit()


def test_settings_invalid_value_is_error_toast_not_500(client, monkeypatch):
    from dragontag.app.config import settings
    from dragontag.app import main as main_mod

    # Keep the watcher untouched if the save were to succeed.
    monkeypatch.setattr(main_mod.watcher, "start", lambda: None)
    monkeypatch.setattr(main_mod.watcher, "stop", lambda: None)

    before = settings().max_recent_changes
    base = {
        "score_threshold": "0.85",
        "filename_template_single": "{track:02d}. {title}.{ext}",
        "filename_template_multidisc": "{track:02d}. {title}.{ext}",
        "multidisc_folder_template": "Disc {disc}",
        **{f"sep_{k}": ";" for k in (
            "ARTIST", "album_artist", "ARTISTS", "ARTISTSORT", "ALBUMARTISTSORT",
            "GENRE", "LABEL", "ISRC", "COMPOSER", "CONDUCTOR", "LYRICIST", "ARRANGER",
        )},
    }

    resp = client.post("/settings", data={**base, "max_recent_changes": "-1"})

    assert resp.status_code == 303  # toast redirect, not a raw 500
    assert "error" in resp.headers["HX-Trigger"]
    assert settings().max_recent_changes == before  # nothing persisted


def test_run_now_refuses_while_same_kind_running(client):
    with session() as s:
        running = Job(
            source_path="", original_name="bg", kind="bulk_retag",
            status=JobStatus.running,
        )
        sched = ScheduledTask(name="x", cron="0 0 * * *", task_type="bulk_retag")
        s.add(running)
        s.add(sched)
        s.commit()
        s.refresh(running)
        s.refresh(sched)
        run_id, sched_id = running.id, sched.id

    try:
        resp = client.post(f"/schedule/{sched_id}/run-now")
        assert resp.status_code == 303
        assert "error" in resp.headers["HX-Trigger"]
        with session() as s:
            row = s.get(ScheduledTask, sched_id)
            assert row.last_status != "ok (manual)"  # dispatch never happened
    finally:
        with session() as s:
            for model, rid in ((Job, run_id), (ScheduledTask, sched_id)):
                obj = s.get(model, rid)
                if obj:
                    s.delete(obj)
            s.commit()
