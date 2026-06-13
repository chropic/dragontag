"""move_back: persist the DB record before the settings write, and roll the
file back if the commit fails (Finding 3)."""
from pathlib import Path

import pytest
import sqlmodel
from sqlmodel import select

from dragontag.app.config import settings, store
from dragontag.app.db import session
from dragontag.app.library.revert import move_back
from dragontag.app.models import FileChange, Track


def _setup(tmp_path) -> tuple[int, int, Path, Path]:
    cur = tmp_path / "lib" / "01. Song.flac"
    cur.parent.mkdir(parents=True, exist_ok=True)
    cur.write_bytes(b"audio")
    orig = tmp_path / "drop" / "song.flac"
    orig.parent.mkdir(parents=True, exist_ok=True)
    with session() as s:
        t = Track(path=str(cur), title="Song")
        s.add(t)
        ch = FileChange(file_path=str(cur), original_path=str(orig), original_name="song.flac")
        s.add(ch)
        s.commit()
        s.refresh(t)
        s.refresh(ch)
        return t.id, ch.id, cur, orig


def _cleanup(tid: int, cid: int) -> None:
    with session() as s:
        for cls, _id in ((Track, tid), (FileChange, cid)):
            row = s.get(cls, _id)
            if row:
                s.delete(row)
        s.commit()


def test_move_back_moves_and_updates_records(tmp_path):
    tid, cid, cur, orig = _setup(tmp_path)
    before = list(settings().scan_exclude_files)
    try:
        ok, msg = move_back(cid)
        assert ok, msg
        assert orig.exists() and not cur.exists()
        with session() as s:
            assert s.get(Track, tid).path == str(orig)
            assert s.get(FileChange, cid).file_path == str(orig)
        assert str(orig) in settings().scan_exclude_files
    finally:
        store().update({"scan_exclude_files": before})
        _cleanup(tid, cid)


def test_move_back_commit_failure_compensates_and_skips_settings(tmp_path, monkeypatch):
    tid, cid, cur, orig = _setup(tmp_path)
    before = list(settings().scan_exclude_files)
    try:
        def _boom(self):
            raise RuntimeError("db down")
        monkeypatch.setattr(sqlmodel.Session, "commit", _boom)

        ok, msg = move_back(cid)
        monkeypatch.undo()

        assert not ok
        # The file is back at its starting location (compensating move)...
        assert cur.exists() and not orig.exists()
        # ...and the persistent exclude list was never touched.
        assert list(settings().scan_exclude_files) == before
    finally:
        monkeypatch.undo()
        store().update({"scan_exclude_files": before})
        _cleanup(tid, cid)


def test_move_back_target_collision_uses_unique_path(tmp_path):
    tid, cid, cur, orig = _setup(tmp_path)
    orig.write_bytes(b"pre-existing")   # original location already occupied
    before = list(settings().scan_exclude_files)
    try:
        ok, msg = move_back(cid)
        assert ok, msg
        assert orig.read_bytes() == b"pre-existing"   # not clobbered
        with session() as s:
            new_path = Path(s.get(FileChange, cid).file_path)
        assert new_path != orig and new_path.exists()
        assert new_path.read_bytes() == b"audio"
    finally:
        store().update({"scan_exclude_files": before})
        _cleanup(tid, cid)
