"""Scheduler-dispatched batches must scan before acting, mirroring the route
layer's unconditional scan prepend — a scheduled organize/re-tag over stale
Track rows moves files based on tags the library no longer has."""
import pytest

from dragontag.app import scheduler, tasks
from dragontag.app.db import session
from dragontag.app.models import LibraryFolder, ScheduledTask


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="sched-test")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    yield fid
    with session() as s:
        row = s.get(LibraryFolder, fid)
        if row:
            s.delete(row)
        s.commit()


@pytest.mark.parametrize("task_type", ["batch_organize", "batch_retag"])
def test_scheduled_batches_scan_first(folder, monkeypatch, task_type):
    captured: dict = {}

    def fake_run_chain(kind, name, steps):
        captured["steps"] = steps
        return 1

    monkeypatch.setattr(tasks, "run_chain", fake_run_chain)

    task = ScheduledTask(
        name="t", cron="0 0 * * *", task_type=task_type,
        params_json={"folder_id": folder},
    )
    scheduler.run_task_by_type(task)

    labels = [label for label, _fn in captured["steps"]]
    assert labels[0].startswith("Scan"), labels
