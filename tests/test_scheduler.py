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

    def fake_enqueue(path, dry_run=False, ctx=None):
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


def test_cleanup_dispatch_runs_chain_with_apply(monkeypatch, tmp_path):
    """The `cleanup` task type dispatches a scan+cleanup chain, forwarding the
    apply flag to cleanup_library."""
    from dragontag.app import scheduler as sch
    from dragontag.app.db import session
    from dragontag.app.library import actions
    from dragontag.app.models import LibraryFolder

    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="t")
        s.add(f); s.commit(); s.refresh(f)
        fid = f.id

    captured = {}
    monkeypatch.setattr(actions, "cleanup_library",
                        lambda folder_id, ctx=None, apply=False: captured.update(apply=apply) or {})
    # scan step is irrelevant here; stub it to a no-op label
    monkeypatch.setattr(sch, "_scan_step", lambda f: [])

    ran = {}
    def fake_run_chain(kind, name, steps):
        for _label, fn in steps:
            fn(_Ctx())
        ran["kind"] = kind
        return 1
    monkeypatch.setattr(sch.tasks, "run_chain", fake_run_chain)

    task = ScheduledTask(name="c", cron="0 0 * * *", task_type="cleanup",
                         params_json={"folder_id": fid, "apply": True})
    sch.run_task_by_type(task)
    assert ran["kind"] == "cleanup"
    assert captured["apply"] is True

    with session() as s:
        s.delete(s.get(LibraryFolder, fid)); s.commit()


def test_registry_sanity():
    """The registry stays small on purpose: helpers, reports, prune, cleanup.
    The batch compositions and structural repair actions were removed — the
    single retag pass through the pipeline replaced them."""
    from dragontag.app.library.actions import LIBRARY_ACTIONS
    assert "cleanup" in LIBRARY_ACTIONS
    for gone in ("reidentify", "fix_album_splits", "unify_artist_folders",
                 "check_album_consistency", "fix_disc_folders", "normalize_filenames"):
        assert gone not in LIBRARY_ACTIONS
    assert "cleanup" in scheduler.TASK_TYPES
    assert "retag" in scheduler.TASK_TYPES
    for gone in ("batch_organize", "batch_retag", "bulk_retag"):
        assert gone not in scheduler.TASK_TYPES
