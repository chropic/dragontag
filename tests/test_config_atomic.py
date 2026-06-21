"""Settings are written atomically so a failed write can't wipe the config.

Regression: ``_save`` used ``Path.write_text`` (non-atomic); a crash mid-write
left a truncated settings.json that ``_load`` silently replaced with defaults,
losing every user setting.
"""
import json
import threading

from dragontag.app import config
from dragontag.app.config import UserSettings


def _isolated_store(tmp_path):
    store = config._Store()
    store._settings_path = tmp_path / "settings.json"   # isolate from the shared file
    return store


def test_save_roundtrip(tmp_path):
    store = _isolated_store(tmp_path)
    store._save(UserSettings(genre_limit=7))
    data = json.loads(store._settings_path.read_text("utf-8"))
    assert data["genre_limit"] == 7


def test_failed_save_preserves_existing_file(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path)
    store._save(UserSettings(genre_limit=3))   # known-good baseline
    good = store._settings_path.read_text("utf-8")

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", _boom)
    store._save(UserSettings(genre_limit=99))   # must fail without corrupting

    assert store._settings_path.read_text("utf-8") == good      # untouched
    assert not (tmp_path / "settings.json.tmp").exists()         # temp cleaned up


def test_transact_serializes_read_modify_write(tmp_path):
    """S3: two concurrent list-append callers (e.g. move_back racing the
    protect toggle, both appending to scan_exclude_files) must not lose an
    append to a lost-update race. Plain ``update()`` can't fix this because
    the read of the current list happens *before* the lock is taken; the
    fn-based ``transact()`` takes the read under the lock instead."""
    store = _isolated_store(tmp_path)
    store.user = UserSettings(scan_exclude_files=[])

    barrier = threading.Barrier(2)

    def _append(path: str) -> None:
        barrier.wait()

        def _patch(cur):
            current = list(cur.scan_exclude_files)
            current.append(path)
            return {"scan_exclude_files": current}

        store.transact(_patch)

    t1 = threading.Thread(target=_append, args=("/a",))
    t2 = threading.Thread(target=_append, args=("/b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(store.user.scan_exclude_files) == ["/a", "/b"]
