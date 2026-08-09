"""Explicit manual re-tag traversal with bounded discovery and queueing."""
from __future__ import annotations

import os
from pathlib import Path

from ..config import settings
from ..library.filters import is_path_excluded
from .pipeline import SUPPORTED_EXTS, enqueue, submit


def enqueue_folder(source_path: Path, *, dry_run: bool | None = None, ctx=None) -> int:
    """Stream a user-selected tree into the bounded ingest queue.

    A directory is persisted before its jobs are submitted, so album election
    sees every sibling without building a global file list.
    """
    if not source_path.is_dir():
        raise ValueError(f"Not a directory: {source_path}")
    cfg = settings()
    count = 0
    for root, dirs, names in os.walk(source_path):
        dirs.sort()
        names.sort()
        paths: list[Path] = []
        for name in names:
            if ctx:
                ctx.check_cancelled()
            path = Path(root) / name
            if path.suffix.lower() not in SUPPORTED_EXTS or not path.is_file():
                continue
            if is_path_excluded(path, cfg.scan_filter_patterns, cfg.scan_exclude_dirs, cfg.scan_exclude_files):
                continue
            paths.append(path)
        if not paths:
            continue
        group_key = str(Path(root).resolve()) if len(paths) >= 2 else None
        ids = [
            enqueue(path, dry_run=dry_run, requeue_reviews=True, group_key=group_key,
                    manual_selection=True).id
            for path in paths
        ]
        for job_id in ids:
            if ctx:
                ctx.check_cancelled()
            submit(job_id, ctx=ctx)
            count += 1
            if ctx:
                ctx.progress(count, item=Path(root).name)
    if ctx:
        ctx.log(f"Manually queued {count} file(s) from {source_path}")
    return count
