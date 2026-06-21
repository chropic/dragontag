"""Per-path locking so the ingest worker and an HTTP-triggered revert/move-back
can't mutate the same physical file at the same time.

Both sides do read-then-write on tags and/or the file's location (snapshot ->
restore, or write_tags -> move); without serializing on the path, a revert
racing the worker's tag write can interleave and corrupt the file or leave the
DB pointing at a location that no longer matches reality.

Locks are looked up by the resolved absolute path so the same file accessed
via different (but equivalent) path strings still serializes correctly.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_meta_lock = threading.Lock()


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


@contextmanager
def path_lock(path: Path) -> Iterator[None]:
    key = _key(path)
    with _meta_lock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        yield
