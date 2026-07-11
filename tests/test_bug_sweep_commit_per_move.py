"""File-moving library actions must commit Track.path per move.

``fix_disc_folders`` and ``normalize_filenames`` used to hold every
``Track.path`` update in one session and commit once after the loop — a
cancel (``TaskCancelled`` from ``ctx.check_cancelled``) or an unguarded
exception mid-run rolled back the updates for files already physically
moved/renamed, leaving the DB pointing at paths that no longer exist.
"""
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.actions import fix_disc_folders, normalize_filenames
from dragontag.app.models import LibraryFolder, Track
from dragontag.app.tasks import TaskCancelled


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


def _add_track(fid: int, path: Path, **kw) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00audio")
    with session() as s:
        t = Track(library_folder_id=fid, path=str(path), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


class _CancelAfter:
    """TaskCtx stand-in whose Stop button 'fires' after n check calls."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    def check_cancelled(self) -> None:
        self.calls += 1
        if self.calls > self.n:
            raise TaskCancelled()

    def progress(self, *a, **k) -> None:
        pass

    def log(self, *a, **k) -> None:
        pass


def test_normalize_filenames_cancel_keeps_committed_renames(folder):
    fid, root = folder
    # Both stems end with a space, so both need a rename.
    t1 = _add_track(fid, root / "Artist" / "Album" / "aaa .mp3")
    t2 = _add_track(fid, root / "Artist" / "Album" / "bbb .mp3")

    with pytest.raises(TaskCancelled):
        normalize_filenames(fid, ctx=_CancelAfter(1))

    with session() as s:
        p1 = Path(s.get(Track, t1).path)
        p2 = Path(s.get(Track, t2).path)
    # First file was renamed on disk before the cancel — its committed DB row
    # must match the disk, not be rolled back to the old (now-missing) path.
    assert p1.name == "aaa.mp3"
    assert p1.exists()
    # Second file was never touched.
    assert p2.name == "bbb .mp3"
    assert p2.exists()


def test_fix_disc_folders_cancel_keeps_committed_flatten(folder):
    fid, root = folder
    # Two single-disc albums each with a stray "Disc 1" subfolder to flatten.
    t1 = _add_track(fid, root / "A1" / "Album" / "Disc 1" / "01. one.mp3")
    t2 = _add_track(fid, root / "A2" / "Album" / "Disc 1" / "01. two.mp3")

    with pytest.raises(TaskCancelled):
        fix_disc_folders(fid, ctx=_CancelAfter(1))

    with session() as s:
        p1 = Path(s.get(Track, t1).path)
        p2 = Path(s.get(Track, t2).path)
    # Albums are processed in sorted order, so A1 flattened before the cancel.
    assert p1 == root / "A1" / "Album" / "01. one.mp3"
    assert p1.exists()
    assert p2 == root / "A2" / "Album" / "Disc 1" / "01. two.mp3"
    assert p2.exists()
