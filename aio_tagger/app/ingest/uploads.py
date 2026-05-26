"""Web-UI upload handler.

Drops every uploaded file into the same ``/drop`` folder the watcher monitors,
then explicitly enqueues it. Going via the drop folder (rather than a
separate ``/tmp/uploads`` area) means uploads and watcher events share one
post-ingest path, and the user can still find/inspect/delete them on disk
between upload and the pipeline picking them up.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from fastapi import UploadFile

from ..config import env
from . import pipeline


async def save_uploads(files: Iterable[UploadFile]) -> list[int]:
    """Persist each upload and submit a pipeline job per file."""
    job_ids: list[int] = []
    drop = env().drop_path
    drop.mkdir(parents=True, exist_ok=True)

    for upload in files:
        # Defensive: ``upload.filename`` is browser-supplied, so strip any
        # path components so the user can't write outside the drop folder.
        name = Path(upload.filename or "uploaded").name
        target = drop / name

        # Uniquify on collision — appending ``-1``, ``-2``, … keeps each
        # upload separate even if the user uploads the same name twice.
        i = 1
        while target.exists():
            target = drop / f"{Path(name).stem}-{i}{Path(name).suffix}"
            i += 1

        # Stream chunks rather than loading the whole file into memory —
        # FLACs from a high-bitrate rip can easily be 100MB+.
        with target.open("wb") as out:
            while chunk := await upload.read(1 << 20):
                out.write(chunk)

        job = pipeline.enqueue(target)
        pipeline.submit(job.id)
        job_ids.append(job.id)

    return job_ids
