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

from fastapi import HTTPException, UploadFile

from ..config import env
from ..library.paths import unique_path
from . import pipeline

# Extensions that are safe to accept. Kept in lock-step with pipeline.SUPPORTED_EXTS.
_ALLOWED_EXTS = {".flac", ".mp3", ".wav", ".m4a", ".mp4"}

# MIME types accepted from browsers. application/octet-stream is only allowed
# when the file extension is also on the allowlist (some browsers send it for
# all audio files they don't recognise).
_ALLOWED_MIME_PREFIXES = ("audio/", "video/mp4")

# Extensions that must never be processed regardless of MIME type.
_EXECUTABLE_EXTS = {
    ".sh", ".bash", ".py", ".rb", ".pl", ".php", ".exe", ".bat", ".cmd",
    ".ps1", ".jar", ".class", ".js", ".mjs", ".ts",
}


def _validate(upload: UploadFile) -> None:
    """Raise HTTPException(422) for any upload that fails security checks."""
    name = Path(upload.filename or "").name
    if not name or name in (".", ".."):
        raise HTTPException(status_code=422, detail="Upload rejected: filename is empty or invalid.")

    suffix = Path(name).suffix.lower()

    if suffix in _EXECUTABLE_EXTS:
        raise HTTPException(status_code=422, detail=f"Upload rejected: file type '{suffix}' is not allowed.")

    if suffix not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"Upload rejected: '{suffix}' is not a supported audio format. Allowed: {', '.join(sorted(_ALLOWED_EXTS))}",
        )

    ct = (upload.content_type or "").lower()
    if ct and ct != "application/octet-stream":
        if not any(ct.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
            raise HTTPException(
                status_code=422,
                detail=f"Upload rejected: MIME type '{ct}' is not an accepted audio type.",
            )


async def save_uploads(files: Iterable[UploadFile]) -> list[int]:
    """Validate, persist each upload, and submit a pipeline job per file."""
    job_ids: list[int] = []
    drop = env().drop_path
    drop.mkdir(parents=True, exist_ok=True)

    for upload in files:
        _validate(upload)

        # Defensive: ``upload.filename`` is browser-supplied, so strip any
        # path components so the user can't write outside the drop folder.
        name = Path(upload.filename or "uploaded").name
        target = unique_path(drop / name)

        # Stream chunks rather than loading the whole file into memory —
        # FLACs from a high-bitrate rip can easily be 100MB+.
        written = 0
        with target.open("wb") as out:
            while chunk := await upload.read(1 << 20):
                written += len(chunk)
                out.write(chunk)

        if written == 0:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Upload rejected: '{name}' is empty.")

        job = pipeline.enqueue(target)
        pipeline.submit(job.id)
        job_ids.append(job.id)

    return job_ids
