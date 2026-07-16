"""New library actions: prune, normalize filenames, validate tags, duplicates."""
from pathlib import Path

import pytest

from dragontag.app.db import session
from dragontag.app.library.actions import (
    find_duplicates,
    prune_library,
    validate_tags,
)
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
        from sqlmodel import select
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


def test_prune_removes_junk_and_empty_dirs_only(folder):
    fid, root = folder
    album = root / "Artist" / "Album"
    _add_track(fid, album / "01. Song.flac")
    (album / "Thumbs.db").write_bytes(b"junk")
    (album / "leftover.tmp").write_bytes(b"junk")
    empty = root / "Empty" / "Deeper"
    empty.mkdir(parents=True)

    out = prune_library(fid)

    assert out["junk_removed"] == 2
    assert not (album / "Thumbs.db").exists()
    assert not (album / "leftover.tmp").exists()
    assert (album / "01. Song.flac").exists()      # audio untouched
    assert not (root / "Empty").exists()           # empty tree pruned
    assert album.exists()


def test_validate_tags_reports_problems(folder):
    fid, root = folder
    _add_track(fid, root / "a.flac", title="Ok", artist="A", album_artist="A",
               track_num=5, track_total=4)        # impossible track number
    _add_track(fid, root / "b.flac", artist="B", album_artist="B")  # missing title

    out = validate_tags(fid)
    assert out["checked"] == 2
    assert out["problems"] >= 2


def test_find_duplicates_groups_by_mbid_and_tags(folder):
    fid, root = folder
    _add_track(fid, root / "x1.flac", mb_track_id="mb-1", title="Song", artist="A", duration=200)
    _add_track(fid, root / "x2.flac", mb_track_id="mb-1", title="Song", artist="A", duration=200)
    _add_track(fid, root / "y1.flac", title="Other", artist="B", duration=100)
    _add_track(fid, root / "y2.flac", title="other", artist="b", duration=101)
    _add_track(fid, root / "z.flac", title="Unique", artist="C", duration=50)

    out = find_duplicates(fid)
    assert out["groups"] == 2
    assert out["files"] == 4
