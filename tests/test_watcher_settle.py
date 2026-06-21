"""M3: the watcher must not ingest a file that is still being written.

``_collect_ready`` only releases a path once its settle window has elapsed
*and* its size has stopped changing.
"""
import time
from pathlib import Path

from dragontag.app.ingest.watcher import _Handler


def _touch_with(handler, p: Path, ts: float, size: int, hits: int = 0) -> None:
    # Bypass _is_ignored/stat and inject a known (timestamp, size, hits) directly.
    with handler._lock:
        handler._pending[p] = (ts, size, hits)


def test_growing_file_is_not_ready(tmp_path):
    h = _Handler()
    p = tmp_path / "big.flac"
    p.write_bytes(b"x" * 100)
    # Recorded size (50) is stale vs the on-disk size (100) → still growing.
    _touch_with(h, p, ts=time.time() - 10, size=50)
    ready = h._collect_ready(now=time.time(), settle=2.0)
    assert ready == []
    # Timer reset with the new size; still pending.
    assert p in h._pending
    assert h._pending[p][1] == 100


def test_stable_file_requires_two_confirmations_before_ready(tmp_path):
    """S7: a single matching stat() sample isn't enough — the size must be
    observed stable on two separate settle-window checks."""
    h = _Handler()
    p = tmp_path / "done.flac"
    p.write_bytes(b"x" * 100)
    _touch_with(h, p, ts=time.time() - 10, size=100)  # size matches disk

    # First check: stable once, but not yet ready.
    first_now = time.time()
    assert h._collect_ready(now=first_now, settle=2.0) == []
    assert p in h._pending
    assert h._pending[p][2] == 1  # one stable hit recorded

    # Second check, a full settle window later: now it's ready.
    ready = h._collect_ready(now=first_now + 2.0, settle=2.0)
    assert ready == [p]
    assert p not in h._pending


def test_size_change_between_confirmations_resets_streak(tmp_path):
    h = _Handler()
    p = tmp_path / "resumed.flac"
    p.write_bytes(b"x" * 100)
    _touch_with(h, p, ts=time.time() - 10, size=100)

    first_now = time.time()
    assert h._collect_ready(now=first_now, settle=2.0) == []
    assert h._pending[p][2] == 1

    # File grew again before the second confirmation — streak must reset.
    p.write_bytes(b"x" * 150)
    assert h._collect_ready(now=first_now + 2.0, settle=2.0) == []
    assert h._pending[p][1] == 150
    assert h._pending[p][2] == 0


def test_within_settle_window_not_ready(tmp_path):
    h = _Handler()
    p = tmp_path / "fresh.flac"
    p.write_bytes(b"x" * 100)
    now = time.time()
    _touch_with(h, p, ts=now, size=100)  # just touched
    assert h._collect_ready(now=now, settle=2.0) == []
    assert p in h._pending


def test_vanished_file_is_dropped(tmp_path):
    h = _Handler()
    p = tmp_path / "ghost.flac"  # never created on disk
    _touch_with(h, p, ts=time.time() - 10, size=10)
    assert h._collect_ready(now=time.time(), settle=2.0) == []
    assert p not in h._pending
