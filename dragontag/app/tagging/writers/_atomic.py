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
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dgtag-", suffix=path.suffix)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        shutil.copy2(path, tmp_path)
        yield tmp_path
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
