"""Background task runner that surfaces long-running maintenance work as Jobs.

Library scans, organizes, scheduled tasks, etc. used to run in anonymous daemon
threads with stderr-only logging. ``run_task`` instead creates a ``Job`` row
with a non-"ingest" ``kind`` so the work shows up in the jobs list, gets a
persistent log, and feeds the universal progress bar (``GET /api/progress``).
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import timedelta
from typing import Any, Callable

from sqlmodel import select

from .db import session
from .models import Job, JobStatus
from .timeutil import now_utc

log = logging.getLogger(__name__)

# Minimum seconds between DB commits from progress()/log() updates, so a tight
# per-file loop doesn't hammer SQLite.
_COMMIT_INTERVAL = 1.0

# A ``running`` Job whose ``updated_at`` hasn't advanced for this long is
# considered stalled and reaped to ``error``. Healthy long tasks heartbeat via
# TaskCtx.progress()/log() (which bump updated_at ~every second), so only a
# genuinely hung or silently-dead task trips this.
STALE_RUNNING_AFTER = timedelta(minutes=15)


def reap_stale_jobs() -> int:
    """Mark heartbeat-stale ``running`` jobs as ``error``. Returns the count.

    Restart recovery (``ingest.pipeline.resubmit_pending``) only runs at boot;
    this catches tasks that hang or die silently *while the process keeps
    running*, which would otherwise wedge ``scheduler._same_kind_running``
    forever and leave a perpetual progress bar in the UI.
    """
    cutoff = now_utc() - STALE_RUNNING_AFTER
    with session() as s:
        rows = s.exec(
            select(Job).where(Job.status == JobStatus.running, Job.updated_at < cutoff)
        ).all()
        for j in rows:
            j.status = JobStatus.error
            j.error = (j.error or "") + "\nReaped: no progress heartbeat (stalled task)"
            j.updated_at = now_utc()
            s.add(j)
            with _cancel_lock:
                _cancel_events.pop(j.id, None)
        if rows:
            s.commit()
            log.warning("reaped %d stalled running job(s)", len(rows))
        return len(rows)

# Cancel events for currently running tasks, keyed by job id. Set via
# request_cancel() (the "Stop" button); task callables notice through
# TaskCtx.cancelled / check_cancelled() at their next loop iteration.
_cancel_events: dict[int, threading.Event] = {}
_cancel_lock = threading.Lock()


class TaskCancelled(Exception):
    """Raised inside a task callable when the user requested a stop."""


def request_cancel(job_id: int) -> bool:
    """Signal a running task to stop. Returns False if no such task is running."""
    with _cancel_lock:
        ev = _cancel_events.get(job_id)
    if ev is None:
        return False
    ev.set()
    return True


class TaskCtx:
    """Handle given to a task callable for progress + log reporting."""

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self._lines: list[str] = []
        self._current: int | None = None
        self._total: int | None = None
        self._item: str | None = None
        self._prefix: str = ""  # set by run_chain to tag the current step
        self._last_commit = 0.0
        self._lock = threading.Lock()
        self._cancel_event: threading.Event | None = None  # set by run_task

    @property
    def cancelled(self) -> bool:
        """True once the user has requested this task to stop."""
        return self._cancel_event is not None and self._cancel_event.is_set()

    def check_cancelled(self) -> None:
        """Raise ``TaskCancelled`` if a stop was requested. Call inside loops."""
        if self.cancelled:
            raise TaskCancelled()

    def log(self, line: str) -> None:
        with self._lock:
            self._lines.append(self._prefix + line.rstrip())
        self._maybe_flush()

    def progress(self, current: int, total: int | None = None, item: str | None = None) -> None:
        with self._lock:
            self._current = current
            if total is not None:
                self._total = total
            if item is not None:
                self._item = self._prefix + item
        self._maybe_flush()

    def _maybe_flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_commit < _COMMIT_INTERVAL:
            return
        self._last_commit = now
        with self._lock:
            lines, self._lines = self._lines, []
            current, total, item = self._current, self._total, self._item
        with session() as s:
            job = s.get(Job, self.job_id)
            if not job:
                return
            if lines:
                job.log = (job.log or "") + "\n".join(lines) + "\n"
            job.progress_current = current
            job.progress_total = total
            job.progress_item = item
            job.updated_at = now_utc()
            s.add(job)
            s.commit()


def run_task(kind: str, name: str, fn: Callable[[TaskCtx], Any]) -> int:
    """Run ``fn`` in a daemon thread, tracked by a Job row. Returns the job id."""
    with session() as s:
        job = Job(source_path="", original_name=name, kind=kind, status=JobStatus.running)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    ctx = TaskCtx(job_id)
    ctx._cancel_event = threading.Event()
    with _cancel_lock:
        _cancel_events[job_id] = ctx._cancel_event

    def _run() -> None:
        try:
            result = fn(ctx)
            ctx._maybe_flush(force=True)
            with session() as s:
                j = s.get(Job, job_id)
                if j:
                    j.status = JobStatus.done
                    if result is not None:
                        j.log = (j.log or "") + f"Result: {result}\n"
                    j.updated_at = now_utc()
                    s.add(j)
                    s.commit()
        except TaskCancelled:
            log.info("task %s (%s) stopped by user", name, kind)
            ctx.log("Stopped by user.")
            ctx._maybe_flush(force=True)
            with session() as s:
                j = s.get(Job, job_id)
                if j:
                    j.status = JobStatus.skipped
                    j.updated_at = now_utc()
                    s.add(j)
                    s.commit()
        except Exception as e:
            log.exception("task %s (%s) failed", name, kind)
            ctx._maybe_flush(force=True)
            with session() as s:
                j = s.get(Job, job_id)
                if j:
                    j.status = JobStatus.error
                    j.error = f"{e}\n{traceback.format_exc()}"
                    j.updated_at = now_utc()
                    s.add(j)
                    s.commit()
        finally:
            with _cancel_lock:
                _cancel_events.pop(job_id, None)

    threading.Thread(target=_run, daemon=True, name=f"dragontag-task-{kind}").start()
    return job_id


def run_chain(kind: str, name: str, steps: list[tuple[str, Callable[[TaskCtx], Any]]]) -> int:
    """Run several task callables sequentially under a single Job row.

    Each step gets the shared ``TaskCtx``; its log lines and progress item are
    prefixed with ``[i/n] label``. A failing step is logged and the chain
    continues — the Job only ends in ``error`` when *every* step failed, so a
    batch run always does as much work as it can.
    """
    total_steps = len(steps)

    def _chain(ctx: TaskCtx) -> dict:
        results: dict[str, Any] = {}
        failures: list[str] = []
        for i, (label, fn) in enumerate(steps, start=1):
            ctx.check_cancelled()
            with ctx._lock:
                ctx._prefix = f"[{i}/{total_steps}] {label}: "
                ctx._current = None
                ctx._total = None
                ctx._item = f"[{i}/{total_steps}] {label}"
            ctx.log("started")
            # Push the reset progress state immediately so the bar can't show
            # the previous step's counters during the throttle window.
            ctx._maybe_flush(force=True)
            try:
                results[label] = fn(ctx)
                ctx.log("finished")
            except TaskCancelled:
                raise  # a stop request aborts the whole chain
            except Exception as e:
                log.exception("chain step %r failed", label)
                failures.append(label)
                ctx.log(f"FAILED: {e}")
        with ctx._lock:
            ctx._prefix = ""
        if failures and len(failures) == total_steps:
            raise RuntimeError(f"all steps failed: {', '.join(failures)}")
        if failures:
            ctx.log(f"Completed with failures: {', '.join(failures)}")
        return results

    return run_task(kind, name, _chain)
