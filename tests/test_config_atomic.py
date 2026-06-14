"""Settings are written atomically so a failed write can't wipe the config.

Regression: ``_save`` used ``Path.write_text`` (non-atomic); a crash mid-write
left a truncated settings.json that ``_load`` silently replaced with defaults,
losing every user setting.
"""
import json

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
