"""Drop-folder watcher built on watchdog.

Watchdog fires events as soon as a write happens, which is too eager: a file
being copied via SMB can fire dozens of ``on_modified`` events while it's
still mid-transfer, and reading it then would either fail or get partial
audio. We defend against that with a *settle window*: each event timestamps
the file in a dict, and a background thread waits for an Event signal before
draining files whose last-modified event is older than
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
from ..library.filters import is_path_excluded
from . import pipeline

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    """Coalesces create/modify/move events per-path with a settle timestamp."""

    def __init__(self) -> None:
        # path -> (last_event_ts, last_seen_size, stable_hits). The size lets
        # us require the file to have *stopped growing* before we ingest it —
        # the settle window alone can't tell "finished" from "stalled
        # mid-transfer". ``stable_hits`` requires the size to match on two
        # separate settle-window checks (not just one stat() call) before
        # declaring the file ready, so a single coincidentally-matching
        # sample (e.g. a paused-but-not-finished SMB/NFS write landing on a
        # round byte count) can't fool us into ingesting mid-transfer.
        self._pending: dict[Path, tuple[float, int, int]] = {}
        self._lock = threading.Lock()
        self._has_pending = threading.Event()
        # Set by watcher.stop(): ends this handler's settle thread. Without it
        # every watcher toggle in Settings leaked one settle thread forever.
        self._stopped = threading.Event()

    def _is_ignored(self, p: Path) -> bool:
        cfg = settings()
        name = p.name
        for pat in cfg.watcher_ignore_patterns:
            if fnmatch.fnmatch(name, pat):
                return True
        if p.suffix.lower() not in pipeline.SUPPORTED_EXTS:
            return True
        return is_path_excluded(
            p, cfg.scan_filter_patterns, cfg.scan_exclude_dirs, cfg.scan_exclude_files
        )

    def on_created(self, event):
        if event.is_directory:
            return
        self._touch(Path(event.src_path))

    def on_modified(self, event):
        if event.is_directory:
            return
        self._touch(Path(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        self._touch(Path(event.dest_path))

    # Number of consecutive settle-window checks that must observe the same
    # size before a file is declared ready.
    _REQUIRED_STABLE_HITS = 2

    def _touch(self, p: Path) -> None:
        if self._is_ignored(p):
            return
        try:
            size = p.stat().st_size
        except OSError:
            return  # vanished/unreadable between event and stat — ignore
        with self._lock:
            self._pending[p] = (time.time(), size, 0)
        self._has_pending.set()

    def _collect_ready(self, now: float, settle: float) -> list[Path]:
        """Return paths whose settle window elapsed *and* whose size has been
        observed stable on two separate checks.

        Files still growing (or only stable once so far) have their timer
        reset; vanished/unreadable files are dropped. Extracted from
        ``settle_loop`` so it can be unit-tested.
        """
        ready: list[Path] = []
        with self._lock:
            for p, (t, seen_size, hits) in list(self._pending.items()):
                if now - t < settle:
                    continue
                # Settle window elapsed; confirm the file has stopped growing
                # before ingesting (guards against a slow/stalled SMB/NFS
                # transfer that merely *looks* idle).
                try:
                    cur_size = p.stat().st_size
                except OSError:
                    del self._pending[p]  # gone/unreadable; drop it
                    continue
                if cur_size != seen_size:
                    # Still changing — reset the timer and the stability streak.
                    self._pending[p] = (now, cur_size, 0)
                elif hits + 1 >= self._REQUIRED_STABLE_HITS:
                    ready.append(p)
                    del self._pending[p]
                else:
                    # Matches the last sample, but we want one more
                    # confirmation a full settle window later before trusting it.
                    self._pending[p] = (now, cur_size, hits + 1)
            if self._pending:
                self._has_pending.set()
        return ready

    def settle_loop(self) -> None:
        """Background loop: wait for file events, then drain after settle window."""
        while not self._stopped.is_set():
            signalled = self._has_pending.wait(timeout=5.0)
            self._has_pending.clear()
            if self._stopped.is_set():
                return
            if not signalled:
                continue
            time.sleep(settings().watcher_settle_seconds)
            ready = self._collect_ready(time.time(), settings().watcher_settle_seconds)
            for p in ready:
                if not p.exists():
                    continue
                try:
                    job = pipeline.enqueue(p)
                    pipeline.submit(job.id)
                    log.info("Enqueued from watcher: %s (job %d)", p, job.id)
                except Exception:
                    log.exception("Failed to enqueue %s", p)


_observer: Observer | None = None
_handler: _Handler | None = None


def start() -> None:
    """Idempotently start the observer + settle thread."""
    global _observer, _handler
    if _observer is not None:
        return
    drop = env().drop_path
    drop.mkdir(parents=True, exist_ok=True)
    _handler = _Handler()
    _observer = Observer()
    _observer.schedule(_handler, str(drop), recursive=True)
    _observer.start()
    threading.Thread(target=_handler.settle_loop, name="dragontag-watcher-settle", daemon=True).start()
    log.info("Watcher started on %s", drop)


def stop() -> None:
    global _observer, _handler
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=5)
        _observer = None
    if _handler is not None:
        # End the settle thread too — the wait() wakes it so it exits promptly.
        _handler._stopped.set()
        _handler._has_pending.set()
        _handler = None
