"""One-shot backup/restore of dragontag's persisted state.

A backup is a versioned ``.tar.gz`` containing a ``manifest.json`` (format
version + sha256 of every member) plus:

* ``dragontag.db``   — consistent snapshot via the sqlite backup API
* ``settings.json``  — UI-editable settings
* ``password.hash``  — wizard-written argon2 hash (if present)
* ``acoustid.key``   — wizard-written AcoustID key (if present)

Restore validates the bundle (manifest, hashes, ``PRAGMA integrity_check``,
settings schema) before any live file is touched, then swaps the files in with
``*.pre-restore`` copies of the old state kept alongside.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
_KEEP_BACKUPS = 10

# Config-dir files included in a bundle (besides the DB). Optional ones are
# skipped when absent.
_CONFIG_FILES = ("settings.json", "password.hash", "acoustid.key")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backups_dir() -> Path:
    from .config import env
    d = env().config_path / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup() -> Path:
    """Write a new backup tarball into ``/config/backups`` and return its path."""
    from .config import env

    config = env().config_path
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out = backups_dir() / f"dragontag-backup-{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as td:
        staging = Path(td)

        # Consistent DB snapshot even while the app is writing.
        db_path = config / "dragontag.db"
        files: dict[str, str] = {}
        if db_path.exists():
            snap = staging / "dragontag.db"
            src = sqlite3.connect(str(db_path))
            try:
                dst = sqlite3.connect(str(snap))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            files["dragontag.db"] = _sha256(snap)

        for name in _CONFIG_FILES:
            p = config / name
            if p.exists():
                target = staging / name
                target.write_bytes(p.read_bytes())
                files[name] = _sha256(target)

        manifest = {
            "app": "dragontag",
            "format_version": FORMAT_VERSION,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "files": files,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        with tarfile.open(out, "w:gz") as tar:
            tar.add(staging / "manifest.json", arcname="manifest.json")
            for name in files:
                tar.add(staging / name, arcname=name)

    _prune_backups()
    log.info("backup written: %s", out)
    return out


def _prune_backups() -> None:
    old = sorted(backups_dir().glob("dragontag-backup-*.tar.gz"))[:-_KEEP_BACKUPS]
    for p in old:
        try:
            p.unlink()
        except OSError:
            pass


def validate_bundle(tar_path: Path) -> dict:
    """Extract + verify a bundle. Returns ``{"manifest": …, "staging": Path}``.

    The caller owns the returned staging directory (a TemporaryDirectory
    object is included under ``"tmpdir"`` to keep it alive). Raises
    ``ValueError`` with a user-readable message on any validation failure.
    """
    if not tar_path.exists():
        raise ValueError(f"No such file: {tar_path}")

    tmpdir = tempfile.TemporaryDirectory()
    staging = Path(tmpdir.name)

    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for m in tar.getmembers():
                # Reject path traversal / absolute members / links.
                name = Path(m.name)
                if name.is_absolute() or ".." in name.parts or not m.isfile():
                    raise ValueError(f"Unsafe bundle member: {m.name}")
            tar.extractall(staging)
    except (tarfile.TarError, EOFError) as e:
        tmpdir.cleanup()
        raise ValueError(f"Not a valid backup archive: {e}")
    except ValueError:
        tmpdir.cleanup()
        raise

    try:
        manifest_path = staging / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("Bundle has no manifest.json — not a dragontag backup.")
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if manifest.get("app") != "dragontag":
            raise ValueError("Bundle was not created by dragontag.")
        if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
            raise ValueError(
                f"Bundle format v{manifest.get('format_version')} is newer than this "
                f"app supports (v{FORMAT_VERSION}). Upgrade dragontag first."
            )

        files = manifest.get("files") or {}
        for name, digest in files.items():
            p = staging / name
            if not p.exists():
                raise ValueError(f"Bundle is missing {name} listed in its manifest.")
            if _sha256(p) != digest:
                raise ValueError(f"Checksum mismatch for {name} — bundle is corrupt.")

        if "dragontag.db" in files:
            conn = sqlite3.connect(str(staging / "dragontag.db"))
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
            finally:
                conn.close()
            if not row or row[0] != "ok":
                raise ValueError("Database in bundle failed its integrity check.")

        if "settings.json" in files:
            from .config import UserSettings
            try:
                UserSettings.model_validate_json(
                    (staging / "settings.json").read_text("utf-8")
                )
            except Exception as e:
                raise ValueError(f"settings.json in bundle is invalid: {e}")

        return {"manifest": manifest, "staging": staging, "tmpdir": tmpdir}
    except ValueError:
        tmpdir.cleanup()
        raise


def restore_bundle(tar_path: Path) -> str:
    """Validate ``tar_path`` and swap its contents into the live config dir.

    Returns a user-readable summary. Raises ``ValueError`` on validation
    failure (live files untouched in that case).
    """
    from .config import env, reset_store
    from .db import reset_engine

    bundle = validate_bundle(tar_path)
    try:
        staging: Path = bundle["staging"]
        files: dict = bundle["manifest"].get("files") or {}
        config = env().config_path

        # Close the live DB before replacing the file under it.
        reset_engine()

        restored = []
        for name in files:
            live = config / name
            if live.exists():
                live.replace(config / f"{name}.pre-restore")
            (staging / name).replace(live)
            restored.append(name)

        reset_store()
        log.info("restore complete: %s", ", ".join(restored))
        return (
            f"Restored {', '.join(restored)} from {tar_path.name}. "
            "Previous files kept as *.pre-restore. Restart the container to be safe."
        )
    finally:
        bundle["tmpdir"].cleanup()
