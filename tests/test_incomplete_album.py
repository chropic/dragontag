"""find_missing_tracks persists IncompleteAlbum rows (MB fetch mocked)."""
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library import actions
from dragontag.app.models import IncompleteAlbum, LibraryFolder, Track


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="inc-test")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    yield fid, tmp_path
    with session() as s:
        for model in (Track, IncompleteAlbum):
            for row in s.exec(select(model).where(model.library_folder_id == fid)).all():
                s.delete(row)
        row = s.get(LibraryFolder, fid)
        if row:
            s.delete(row)
        s.commit()


def _add_track(fid: int, path: Path, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")
    with session() as s:
        s.add(Track(library_folder_id=fid, path=str(path), **kw))
        s.commit()


def _fake_release(track_count: int):
    return {
        "medium-list": [{
            "position": "1",
            "track-count": track_count,
            "track-list": [
                {"position": str(i), "recording": {"title": f"Track {i}"}}
                for i in range(1, track_count + 1)
            ],
        }]
    }


def test_incomplete_album_rows_written_and_replaced(folder, monkeypatch):
    fid, root = folder
    _add_track(fid, root / "A" / "01.flac", album="Half Album", artist="A",
               mb_album_id="rel-1", track_num=1, disc_num=1)

    monkeypatch.setattr(
        "dragontag.app.identify.musicbrainz.fetch_release",
        lambda rid: _fake_release(3),
    )
    out = actions.find_missing_tracks(fid)
    assert out["count"] == 1

    with session() as s:
        rows = s.exec(select(IncompleteAlbum).where(
            IncompleteAlbum.library_folder_id == fid)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.local_count == 1 and row.expected_count == 3
        assert "1-02. Track 2" in row.missing_titles_json

    # Album becomes complete → rerun wipes the stale row.
    _add_track(fid, root / "A" / "02.flac", album="Half Album", artist="A",
               mb_album_id="rel-1", track_num=2, disc_num=1)
    _add_track(fid, root / "A" / "03.flac", album="Half Album", artist="A",
               mb_album_id="rel-1", track_num=3, disc_num=1)
    out = actions.find_missing_tracks(fid)
    assert out["count"] == 0
    with session() as s:
        assert not s.exec(select(IncompleteAlbum).where(
            IncompleteAlbum.library_folder_id == fid)).all()
