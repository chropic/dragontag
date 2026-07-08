"""A scan must prune Track rows whose file no longer exists — phantom rows
inflate the dashboard counters and produce spurious organizer errors.
"""
import wave
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.scanner import scan_folder
from dragontag.app.models import LibraryFolder, Track


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="test")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    yield fid, tmp_path
    with session() as s:
        for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all():
            s.delete(t)
        row = s.get(LibraryFolder, fid)
        if row:
            s.delete(row)
        s.commit()


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_scan_prunes_rows_for_deleted_files(folder):
    fid, root = folder
    kept = root / "keep.wav"
    gone = root / "gone.wav"
    _make_wav(kept)
    _make_wav(gone)

    scan_folder(root, fid)
    with session() as s:
        assert len(s.exec(select(Track).where(Track.library_folder_id == fid)).all()) == 2

    gone.unlink()
    scan_folder(root, fid)

    with session() as s:
        rows = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
        assert [Path(r.path).name for r in rows] == ["keep.wav"]
