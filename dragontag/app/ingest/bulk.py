"""Bulk-retag: walk a source folder and enqueue every audio file.

Each file goes through the full identify → tag → move pipeline exactly as if
it had been dropped into the drop folder.  The pipeline's serial worker queue
handles backpressure — all jobs are enqueued immediately but processed one at
a time.

Known limitation: for very large folders the rglob walk and DB inserts happen
in the HTTP request thread before the redirect.  At ~1 ms per insert this is
acceptable for typical library sizes; a background-thread solution can be
added later if needed.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .pipeline import SUPPORTED_EXTS, enqueue, submit

log = logging.getLogger(__name__)


def enqueue_folder(source_path: Path) -> list[int]:
    """Enqueue all supported audio files under source_path for re-tagging.

    Raises ``ValueError`` if source_path is not an existing directory.
    Returns the list of created job IDs.
    """
    if not source_path.exists() or not source_path.is_dir():
        raise ValueError(f"Not a directory: {source_path}")

    job_ids: list[int] = []
    for p in sorted(source_path.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTS:
            continue
        job = enqueue(p)
        submit(job.id)
        job_ids.append(job.id)
        log.info("bulk: enqueued %s (job %d)", p.name, job.id)

    log.info("bulk: %d jobs enqueued from %s", len(job_ids), source_path)
    return job_ids
