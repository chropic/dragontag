"""Scheduler dispatch normalizes dry_run to a real bool (Finding 7)."""
import dragontag.app.ingest.bulk as bulk
from dragontag.app import scheduler
from dragontag.app.models import ScheduledTask


class _Ctx:
    def log(self, *a, **k):
        pass

    def progress(self, *a, **k):
        pass

    def check_cancelled(self):
        pass


def _run_dispatch(monkeypatch, params) -> dict:
    captured: dict = {}

    def fake_enqueue(path, dry_run=False):
        captured["dry_run"] = dry_run
        return ["job"]

    def fake_run_task(kind, name, fn):
        fn(_Ctx())          # run synchronously instead of in a daemon thread
        return 1

    monkeypatch.setattr(bulk, "enqueue_folder", fake_enqueue)
    monkeypatch.setattr(scheduler.tasks, "run_task", fake_run_task)

    task = ScheduledTask(name="x", cron="0 0 * * *", task_type="bulk_retag", params_json=params)
    scheduler.run_task_by_type(task)
    return captured


def test_bulk_retag_truthy_dry_run_becomes_true(monkeypatch):
    captured = _run_dispatch(monkeypatch, {"source_path": "/some/path", "dry_run": 1})
    assert captured["dry_run"] is True


def test_bulk_retag_missing_dry_run_becomes_false(monkeypatch):
    captured = _run_dispatch(monkeypatch, {"source_path": "/some/path"})
    assert captured["dry_run"] is False
