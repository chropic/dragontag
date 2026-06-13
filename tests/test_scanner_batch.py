"""Scanner batch indexing must isolate a failing file to itself.

Regression for the bug where one unreadable file in a 50-file batch called
``s.rollback()`` and silently discarded every already-upserted Track in that
batch. The fix gives each file its own SAVEPOINT (``s.begin_nested()``).
"""
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library import scanner
from dragontag.app.models import LibraryFolder, Track


@pytest.fixture()
def folder(tmp_path):
    """A LibraryFolder row pointing at tmp_path; deleted (with its tracks) after."""
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


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00audio")


def _indexed_names(fid: int) -> set[str]:
    with session() as s:
        return {
            Path(t.path).name
            for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all()
        }


def _patch_reader(monkeypatch, bad: set[str]) -> None:
    """Make scanner.read_existing raise for the named files, succeed otherwise."""
    def fake_read(path):
        if Path(path).name in bad:
            raise RuntimeError("unreadable file")
        return {"title": Path(path).stem, "duration": None}
    monkeypatch.setattr(scanner, "read_existing", fake_read)


def test_one_bad_file_does_not_drop_the_batch(folder, monkeypatch):
    fid, root = folder
    for n in ("a.flac", "b.flac", "c.flac"):
        _touch(root / n)
    _patch_reader(monkeypatch, bad={"b.flac"})

    scanner.scan_folder(root, fid)

    persisted = _indexed_names(fid)
    assert "a.flac" in persisted      # would be lost under the old rollback bug
    assert "c.flac" in persisted
    assert "b.flac" not in persisted


def test_bad_file_in_a_multi_batch_run(folder, monkeypatch):
    fid, root = folder
    names = [f"f{i:02d}.flac" for i in range(60)]  # crosses _BATCH_SIZE = 50
    for n in names:
        _touch(root / n)
    _patch_reader(monkeypatch, bad={"f25.flac"})

    scanner.scan_folder(root, fid)

    persisted = _indexed_names(fid)
    assert len(persisted) == 59
    assert "f25.flac" not in persisted
    assert "f24.flac" in persisted and "f26.flac" in persisted


def test_all_good_files_persist(folder, monkeypatch):
    fid, root = folder
    for n in ("a.flac", "b.flac", "c.flac"):
        _touch(root / n)
    _patch_reader(monkeypatch, bad=set())

    count = scanner.scan_folder(root, fid)

    assert count == 3
    assert _indexed_names(fid) == {"a.flac", "b.flac", "c.flac"}


def test_rescan_updates_existing_rows(folder, monkeypatch):
    fid, root = folder
    _touch(root / "a.flac")

    monkeypatch.setattr(scanner, "read_existing", lambda p: {"title": "first", "duration": None})
    scanner.scan_folder(root, fid)
    monkeypatch.setattr(scanner, "read_existing", lambda p: {"title": "second", "duration": None})
    scanner.scan_folder(root, fid)

    with session() as s:
        rows = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
    assert len(rows) == 1                # upsert, not duplicate
    assert rows[0].title == "second"
