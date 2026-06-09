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
from datetime import datetime
from typing import Any, Callable

from .db import session
from .models import Job, JobStatus

log = logging.getLogger(__name__)

# Minimum seconds between DB commits from progress()/log() updates, so a tight
# per-file loop doesn't hammer SQLite.
_COMMIT_INTERVAL = 1.0


class TaskCtx:
    """Handle given to a task callable for progress + log reporting."""

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self._lines: list[str] = []
        self._current: int | None = None
        self._total: int | None = None
        self._last_commit = 0.0
        self._lock = threading.Lock()

    def log(self, line: str) -> None:
        with self._lock:
            self._lines.append(line.rstrip())
        self._maybe_flush()

    def progress(self, current: int, total: int | None = None) -> None:
        with self._lock:
            self._current = current
            if total is not None:
                self._total = total
        self._maybe_flush()

    def _maybe_flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_commit < _COMMIT_INTERVAL:
            return
        self._last_commit = now
        with self._lock:
            lines, self._lines = self._lines, []
            current, total = self._current, self._total
        with session() as s:
            job = s.get(Job, self.job_id)
            if not job:
                return
            if lines:
                job.log = (job.log or "") + "\n".join(lines) + "\n"
            job.progress_current = current
            job.progress_total = total
            job.updated_at = datetime.utcnow()
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
                    j.updated_at = datetime.utcnow()
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
                    j.updated_at = datetime.utcnow()
                    s.add(j)
                    s.commit()

    threading.Thread(target=_run, daemon=True, name=f"dragontag-task-{kind}").start()
    return job_id
