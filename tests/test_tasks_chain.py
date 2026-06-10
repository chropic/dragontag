"""tasks.run_chain: ordering, failure tolerance, progress_item persistence."""
import time

from dragontag.app import tasks
from dragontag.app.db import session
from dragontag.app.models import Job, JobStatus


def _wait_for(job_id: int, timeout: float = 10.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session() as s:
            j = s.get(Job, job_id)
            if j and j.status in (JobStatus.done, JobStatus.error):
                s.expunge(j)
                return j
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_chain_runs_steps_in_order():
    calls: list[str] = []
    steps = [
        ("one", lambda ctx: calls.append("one")),
        ("two", lambda ctx: calls.append("two")),
        ("three", lambda ctx: calls.append("three")),
    ]
    job = _wait_for(tasks.run_chain("test_chain", "ordered chain", steps))
    assert calls == ["one", "two", "three"]
    assert job.status == JobStatus.done
    assert "[1/3] one" in job.log and "[3/3] three" in job.log


def test_chain_continues_past_failed_step():
    calls: list[str] = []

    def boom(ctx):
        raise RuntimeError("kaboom")

    steps = [
        ("first", lambda ctx: calls.append("first")),
        ("bad", boom),
        ("last", lambda ctx: calls.append("last")),
    ]
    job = _wait_for(tasks.run_chain("test_chain", "failing chain", steps))
    assert calls == ["first", "last"]
    assert job.status == JobStatus.done  # only one step failed
    assert "FAILED: kaboom" in job.log
    assert "Completed with failures: bad" in job.log


def test_chain_errors_when_all_steps_fail():
    def boom(ctx):
        raise RuntimeError("nope")

    job = _wait_for(tasks.run_chain("test_chain", "doomed chain", [("only", boom)]))
    assert job.status == JobStatus.error


def test_progress_item_persisted():
    def report(ctx):
        ctx.progress(1, 2, item="some-file.flac")
        ctx._maybe_flush(force=True)

    job = _wait_for(tasks.run_task("test_task", "progress task", report))
    assert job.status == JobStatus.done
    # progress_item carries the last reported item label
    assert job.progress_item and "some-file.flac" in job.progress_item
