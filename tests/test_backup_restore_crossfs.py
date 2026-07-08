"""Restore must survive the normal Docker layout where the validation staging
dir (system /tmp) and /config live on different filesystems. ``os.replace``
raises EXDEV there — and the old code hit it *after* renaming the live DB
away, so a failed restore left no database at all.

The cross-device boundary is simulated by making every rename/replace between
the config dir and the outside world raise EXDEV, while same-side renames
succeed — shutil.move's copy fallback is what must bridge the gap.
"""
import errno
import os
from pathlib import Path

from dragontag.app import backup as bk
from dragontag.app.config import env, settings, store


def _cross_fs_guard(real, config_root: Path):
    def wrapper(a, b, *args, **kw):
        pa, pb = Path(a).resolve(), Path(b).resolve()
        a_in = str(pa).startswith(str(config_root))
        b_in = str(pb).startswith(str(config_root))
        if a_in != b_in:
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(a), 0, str(b))
        return real(a, b, *args, **kw)
    return wrapper


def test_restore_bundle_survives_cross_device_staging(monkeypatch):
    config_root = env().config_path.resolve()

    # Seed a known settings value, snapshot it into a bundle, then change it —
    # a successful restore must bring the old value back.
    store().update({"genre_limit": 7})
    bundle_path = bk.create_backup()
    store().update({"genre_limit": 2})
    assert settings().genre_limit == 2

    monkeypatch.setattr(os, "rename", _cross_fs_guard(os.rename, config_root))
    monkeypatch.setattr(os, "replace", _cross_fs_guard(os.replace, config_root))

    message = bk.restore_bundle(bundle_path)

    assert "settings.json" in message
    assert settings().genre_limit == 7
    # The pre-restore safety copy exists and no live file went missing.
    assert (config_root / "settings.json").exists()
    assert (config_root / "settings.json.pre-restore").exists()
    # No leftover swap staging directory.
    assert not list(config_root.glob(".dgrestore-*"))
