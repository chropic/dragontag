"""Atomic in-place file mutation for tag writes.

mutagen rewrites audio files in place, so a crash mid-``save()`` (OOM, SIGKILL,
power loss, container reclaim) can leave the user's *only* copy of a track
truncated and unplayable. ``atomic_inplace`` removes that window: the writer
mutates a temp copy in the same directory and we ``os.replace`` it back, which
is atomic within a single filesystem. A crash can only ever damage the
throwaway temp, never the original.

Trade-off: this copies the audio bytes and transiently doubles the file's
on-disk size. That's the correct price for never corrupting irreplaceable
audio in a tagger.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path


@contextmanager
def atomic_inplace(path: Path) -> Iterator[Path]:
    """Yield a temp copy of ``path`` for mutation, then atomically swap it in.

    The temp lives in ``path``'s own directory so ``os.replace`` stays atomic
    on the library's filesystem (including the interior of an NFS/SMB mount).
    ``shutil.copy2`` preserves the file mode and mtime. On any exception the
    temp is removed and the original is left untouched.

    The temp's data is fsync'd before the rename, and the containing
    directory is fsync'd after, so the swap is durable across a real crash
    (power loss, OOM kill) rather than just a Python exception — without
    this, ``os.replace`` can land in the page cache and a crash right after
    can leave the rename undone or the data behind it lost.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dgtag-", suffix=path.suffix)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        shutil.copy2(path, tmp_path)
        yield tmp_path
        _fsync_file(tmp_path)
        os.replace(tmp_path, path)
        _fsync_dir(path.parent)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _fsync_file(p: Path) -> None:
    # Windows' ``FlushFileBuffers`` rejects the read-only descriptor produced
    # by ``os.open(..., O_RDONLY)``. A writable binary handle works on Windows
    # and POSIX and does not alter the already-written bytes.
    with p.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(d: Path) -> None:
    # Windows does not support opening a directory with ``os.open`` for
    # ``fsync`` (it raises EBADF after the atomic replacement already
    # succeeded). The rename is still atomic there; directory durability is a
    # POSIX-only strengthening.
    if os.name == "nt":
        return
    fd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# A crash between mkstemp and os.replace leaves a ``.dgtag-*`` orphan behind
# forever (the in-process cleanup in the except branch above never runs).
# Call this once at startup to sweep them out of the library before they
# accumulate.
def cleanup_orphaned_temp_files(root: Path) -> int:
    removed = 0
    for p in root.rglob(".dgtag-*"):
        if p.is_file():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed
