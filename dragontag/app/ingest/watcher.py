"""Drop-folder watcher built on watchdog.

Watchdog fires events as soon as a write happens, which is too eager: a file
being copied via SMB can fire dozens of ``on_modified`` events while it's
still mid-transfer, and reading it then would either fail or get partial
audio. We defend against that with a *settle window*: each event timestamps
the file in a dict, and a separate thread polls the dict every second,
enqueueing files whose last-modified event is older than
``watcher_settle_seconds`` (default 2s).
"""
from __future__ import annotations

import fnmatch
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..config import env, settings
from . import pipeline

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    """Coalesces create/modify/move events per-path with a settle timestamp."""

    def __init__(self) -> None:
        # path -> timestamp of most recent event for that path
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()

    def _is_ignored(self, p: Path) -> bool:
        # Two filters: user-configured glob patterns (``*.part`` etc.) and
        # the hard extension whitelist (we don't even *try* to process .txt).
        name = p.name
        for pat in settings().watcher_ignore_patterns:
            if fnmatch.fnmatch(name, pat):
                return True
        return p.suffix.lower() not in pipeline.SUPPORTED_EXTS

    def on_created(self, event):
        if event.is_directory:
            return
        self._touch(Path(event.src_path))

    def on_modified(self, event):
        if event.is_directory:
            return
        self._touch(Path(event.src_path))

    def on_moved(self, event):
        # A move *into* the drop folder appears as ``on_moved`` with the
        # destination in ``dest_path``. Filebrowser-style atomic writes also
        # land here.
        if event.is_directory:
            return
        self._touch(Path(event.dest_path))

    def _touch(self, p: Path) -> None:
        if self._is_ignored(p):
            return
        with self._lock:
            self._pending[p] = time.time()

    def settle_loop(self) -> None:
        """Background loop: every second, enqueue anything that's been quiet."""
        while True:
            time.sleep(1.0)
            settle = settings().watcher_settle_seconds
            now = time.time()
            ready: list[Path] = []
            with self._lock:
                for p, t in list(self._pending.items()):
                    if now - t >= settle:
                        ready.append(p)
                        del self._pending[p]
            for p in ready:
                if not p.exists():
                    # File could have been deleted/moved before the settle
                    # window expired (e.g. user dragged it back out).
                    continue
                try:
                    job = pipeline.enqueue(p)
                    pipeline.submit(job.id)
                    log.info("Enqueued from watcher: %s (job %d)", p, job.id)
                except Exception:
                    log.exception("Failed to enqueue %s", p)


_observer: Observer | None = None


def start() -> None:
    """Idempotently start the observer + settle thread."""
    global _observer
    if _observer is not None:
        return
    drop = env().drop_path
    drop.mkdir(parents=True, exist_ok=True)
    handler = _Handler()
    _observer = Observer()
    _observer.schedule(handler, str(drop), recursive=True)
    _observer.start()
    threading.Thread(target=handler.settle_loop, name="aio-watcher-settle", daemon=True).start()
    log.info("Watcher started on %s", drop)


def stop() -> None:
    global _observer
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        _observer = None
