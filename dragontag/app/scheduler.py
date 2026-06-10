"""Cron scheduler for recurring maintenance tasks.

A single daemon thread wakes every 30 seconds, computes which enabled
``ScheduledTask`` rows are due (standard 5-field cron expressions via
``croniter``), and dispatches each through ``tasks.run_task`` so scheduled runs
appear in the jobs list like any other background task.

Deliberately simple — no APScheduler, no persistence of missed runs: if the
container was down when a task was due, the run is skipped and the next
occurrence is computed from "now".
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from croniter import croniter
from sqlmodel import select

from . import tasks
from .db import session
from .models import Job, JobStatus, LibraryFolder, ScheduledTask

log = logging.getLogger(__name__)

_POLL_SECONDS = 30.0

# task_type -> human label, shown in the Schedule UI.
TASK_TYPES = {
    "scan": "Scan library folder",
    "organize": "Organize library folder",
    "batch_organize": "Organize batch (organize + cleanup actions)",
    "bulk_retag": "Full re-tag of a folder",
    "batch_retag": "Re-tag batch (QA + advisories + ReplayGain + pipeline)",
    "fetch_lyrics": "Fetch lyrics for folder",
    "fetch_covers": "Fetch cover art for folder",
    "backup": "Create config backup",
}


def is_valid_cron(expr: str) -> bool:
    return croniter.is_valid(expr)


def describe_cron(expr: str) -> str | None:
    """Human-readable description of a cron expression ("At 06:00 AM, only on
    Tuesday"), or None when the expression is invalid."""
    if not is_valid_cron(expr):
        return None
    try:
        from cron_descriptor import get_description
        return get_description(expr)
    except Exception:
        return None


def next_run(expr: str, base: datetime | None = None) -> datetime | None:
    try:
        return croniter(expr, base or datetime.utcnow()).get_next(datetime)
    except Exception:
        return None


def _folder(folder_id: int) -> LibraryFolder | None:
    with session() as s:
        return s.get(LibraryFolder, folder_id)


def run_task_by_type(task: ScheduledTask) -> int | None:
    """Dispatch one scheduled task as a tracked Job. Returns the job id."""
    params = task.params_json or {}
    kind = task.task_type
    name = task.name or TASK_TYPES.get(kind, kind)

    if kind == "scan":
        f = _folder(int(params.get("folder_id", 0)))
        if not f:
            raise ValueError("library folder not found")
        from .library.scanner import scan_folder
        path, fid = Path(f.path), f.id
        return tasks.run_task("scan", name, lambda ctx: scan_folder(path, fid, ctx=ctx))

    if kind == "organize":
        f = _folder(int(params.get("folder_id", 0)))
        if not f:
            raise ValueError("library folder not found")
        from .library.organizer import organize_folder
        fid = f.id
        return tasks.run_task("organize", name, lambda ctx: organize_folder(fid, ctx=ctx))

    if kind == "batch_organize":
        f = _folder(int(params.get("folder_id", 0)))
        if not f:
            raise ValueError("library folder not found")
        from .library.actions import BATCH_ORGANIZE, build_chain_steps
        from .library.organizer import organize_folder
        fid = f.id
        steps = [("Organize files", lambda ctx: organize_folder(fid, ctx=ctx))]
        steps += build_chain_steps(BATCH_ORGANIZE, fid)
        return tasks.run_chain("batch_organize", name, steps)

    if kind == "batch_retag":
        f = _folder(int(params.get("folder_id", 0)))
        if not f:
            raise ValueError("library folder not found")
        from .ingest.bulk import enqueue_folder
        from .library.actions import BATCH_RETAG, build_chain_steps
        fid, fpath = f.id, Path(f.path)
        dry = params.get("dry_run")

        def _enqueue(ctx):
            ids = enqueue_folder(fpath, dry_run=bool(dry))
            ctx.log(f"Enqueued {len(ids)} file(s) for identify → tag → move")
            return {"enqueued": len(ids)}

        steps = build_chain_steps(BATCH_RETAG, fid) + [("Re-tag pipeline", _enqueue)]
        return tasks.run_chain("batch_retag", name, steps)

    if kind == "bulk_retag":
        src = str(params.get("source_path") or "").strip()
        if not src:
            raise ValueError("source_path required")
        dry = params.get("dry_run")
        from .ingest.bulk import enqueue_folder

        def _run(ctx):
            ids = enqueue_folder(Path(src), dry_run=dry)
            ctx.log(f"Enqueued {len(ids)} file(s) from {src}")
            return f"{len(ids)} jobs enqueued"

        return tasks.run_task("bulk_retag", name, _run)

    if kind == "fetch_lyrics":
        fid = int(params.get("folder_id", 0))
        from .library.actions import fetch_lyrics_for_folder
        return tasks.run_task("fetch_lyrics", name, lambda ctx: fetch_lyrics_for_folder(fid, ctx=ctx))

    if kind == "fetch_covers":
        fid = int(params.get("folder_id", 0))
        from .library.actions import fetch_covers_for_folder
        return tasks.run_task("fetch_covers", name, lambda ctx: fetch_covers_for_folder(fid, ctx=ctx))

    if kind == "backup":
        from .backup import create_backup
        return tasks.run_task("backup", name, lambda ctx: str(create_backup()))

    raise ValueError(f"unknown task type: {kind}")


def _same_kind_running(kind: str) -> bool:
    with session() as s:
        row = s.exec(
            select(Job).where(Job.kind == kind, Job.status == JobStatus.running)
        ).first()
        return row is not None


def _tick() -> None:
    now = datetime.utcnow()
    with session() as s:
        rows = s.exec(select(ScheduledTask)).all()

    for t in rows:
        # Refresh next_run_at for display (and as the due-ness marker).
        base = t.last_run_at or t.created_at
        due_at = next_run(t.cron, base)
        with session() as s:
            row = s.get(ScheduledTask, t.id)
            if not row:
                continue
            row.next_run_at = due_at if t.enabled else None
            if not t.enabled or due_at is None or due_at > now:
                s.add(row)
                s.commit()
                continue

            # Due. Skip if a same-kind task is still running.
            if _same_kind_running(row.task_type):
                row.last_status = "skipped: previous run still active"
                row.last_run_at = now
            else:
                try:
                    run_task_by_type(row)
                    row.last_status = "ok"
                except Exception as e:  # noqa: BLE001
                    log.exception("scheduled task %s failed to dispatch", row.name)
                    row.last_status = f"error: {e}"
                row.last_run_at = now
            row.next_run_at = next_run(row.cron, now)
            s.add(row)
            s.commit()


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception:
            log.exception("scheduler tick failed")
        time.sleep(_POLL_SECONDS)


_started = False


def start() -> None:
    """Idempotently start the scheduler thread (called from app startup)."""
    global _started
    if _started:
        return
    threading.Thread(target=_loop, name="dragontag-scheduler", daemon=True).start()
    _started = True
