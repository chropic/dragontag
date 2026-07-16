"""Bulk-retag: walk a source folder and enqueue every audio file.

Each file goes through the full identify → tag → move pipeline exactly as if
it had been dropped into the drop folder.  The pipeline's serial worker queue
handles backpressure — all jobs are enqueued immediately but processed one at
a time.

Callers run this inside ``tasks.run_task`` (the walk + per-file DB inserts on
a large folder take long enough to hang a browser if done in the request
thread); pass the task's ``ctx`` for progress reporting and cancellation.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from ..library.filters import is_path_excluded
from .pipeline import SUPPORTED_EXTS, enqueue, submit

log = logging.getLogger(__name__)


def enqueue_folder(source_path: Path, *, dry_run: bool | None = None, ctx=None) -> list[int]:
    """Enqueue all supported audio files under source_path for re-tagging.

    ``dry_run`` is passed through as a per-job override (see ``pipeline.enqueue``).
    ``ctx`` is an optional ``tasks.TaskCtx`` for progress/cancellation.
    Raises ``ValueError`` if source_path is not an existing directory.
    Returns the list of created job IDs.
    """
    if not source_path.exists() or not source_path.is_dir():
        raise ValueError(f"Not a directory: {source_path}")

    cfg = settings()
    files: list[Path] = []
    for p in sorted(source_path.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if is_path_excluded(
            p, cfg.scan_filter_patterns, cfg.scan_exclude_dirs, cfg.scan_exclude_files
        ):
            continue
        files.append(p)

    # Album grouping: files sharing a parent directory are one album — the
    # pipeline elects a single MusicBrainz release for the group so their
    # release-level tags can't scatter across editions. Loose singles (only
    # file in their directory) keep the per-track path.
    from collections import Counter
    per_parent = Counter(p.parent for p in files)

    job_ids: list[int] = []
    if ctx:
        ctx.progress(0, len(files))
    for i, p in enumerate(files, start=1):
        if ctx:
            ctx.check_cancelled()
            ctx.progress(i, len(files), item=p.name)
        group_key = str(p.parent.resolve()) if per_parent[p.parent] >= 2 else None
        # requeue_reviews: an explicit re-tag should reprocess files whose
        # previous run got stuck in needs_review, not silently skip them.
        job = enqueue(p, dry_run=dry_run, requeue_reviews=True, group_key=group_key)
        submit(job.id)
        job_ids.append(job.id)
        log.info("bulk: enqueued %s (job %d)", p.name, job.id)

    log.info("bulk: %d jobs enqueued from %s", len(job_ids), source_path)
    return job_ids
