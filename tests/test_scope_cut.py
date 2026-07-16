"""The scope cut: batch compositions, nuclear mode and the structural repair
actions are gone. Their routes must 404/405, retired schedule rows must be
disabled (not deleted) at boot, and the legacy ``bulk_retag`` schedule kind
must still dispatch as the new ``retag``."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app import scheduler
from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import ScheduledTask


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


REMOVED_ROUTES = [
    "/library/batch/organize",
    "/library/batch/retag",
    "/library/batch/nuclear",
    "/library/run-selected",
    "/library/fix-album-splits",
    "/library/unify-artist-folders",
    "/library/fix-disc-folders",
    "/library/normalize-filenames",
    "/library/reidentify",
]


def test_removed_routes_are_gone(client):
    for route in REMOVED_ROUTES:
        resp = client.post(route, data={"folder_id": "1"})
        assert resp.status_code in (404, 405), f"{route} still exists ({resp.status_code})"


def test_schedule_create_rejects_retired_kinds(client):
    for kind in ("batch_organize", "batch_retag", "bulk_retag"):
        resp = client.post("/schedule", data={
            "name": "x", "cron": "0 6 * * *", "task_type": kind, "folder_id": "1",
        })
        assert resp.status_code == 303
        assert "error" in resp.headers.get("HX-Trigger", "")
    with session() as s:
        assert not s.exec(
            select(ScheduledTask).where(ScheduledTask.task_type.in_(
                ["batch_organize", "batch_retag", "bulk_retag"]
            ))
        ).all()


def test_retired_schedule_rows_disabled_at_boot():
    with session() as s:
        row = ScheduledTask(
            name="old organize batch", cron="0 6 * * *",
            task_type="batch_organize", params_json={"folder_id": 1}, enabled=True,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        rid = row.id

    scheduler._disable_retired_tasks()

    with session() as s:
        row = s.get(ScheduledTask, rid)
        assert row is not None            # never deleted
        assert row.enabled is False
        assert "removed" in (row.last_status or "")
        s.delete(row)
        s.commit()


def test_legacy_bulk_retag_alias_dispatches_as_retag(tmp_path, monkeypatch):
    captured = {}

    def fake_run_task(kind, name, fn):
        captured["kind"] = kind
        return 1

    monkeypatch.setattr(scheduler.tasks, "run_task", fake_run_task)
    task = ScheduledTask(
        name="legacy", cron="0 6 * * *", task_type="bulk_retag",
        params_json={"source_path": str(tmp_path)},
    )
    scheduler.run_task_by_type(task)
    assert captured["kind"] == "retag"
