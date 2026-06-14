"""organize_folder: moves files to canonical paths and keeps the DB in sync,
including rolling the file back on a DB failure (Finding 3)."""
from pathlib import Path

import pytest
import sqlmodel
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.organizer import _track_to_tags, organize_folder
from dragontag.app.library.paths import build_destination
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


def _add_track(fid: int, path: Path, **kw) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    with session() as s:
        t = Track(library_folder_id=fid, path=str(path), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


_TAGS = dict(title="Song", artist="Artist", album="Album", album_artist="Artist", track_num=1)


def _canonical(fid: int, tid: int, root: Path) -> Path:
    with session() as s:
        track = s.get(Track, tid)
        return build_destination(_track_to_tags(track), ".flac", library_root=root)


def test_organize_moves_and_updates_path(folder):
    fid, root = folder
    src = root / "loose" / "whatever.flac"
    tid = _add_track(fid, src, **_TAGS)
    dest = _canonical(fid, tid, root)

    out = organize_folder(fid)

    assert out["moved"] == 1
    assert dest.exists() and not src.exists()
    with session() as s:
        assert s.get(Track, tid).path == str(dest)


def test_organize_skips_already_canonical(folder):
    fid, root = folder
    # Place the file at its canonical path up front → nothing to move.
    from dragontag.app.tagging.schema import TrackTags
    tags = TrackTags(title="Song", artist_display="Artist", album="Album",
                     album_artist_display="Artist", track=1)
    dest = build_destination(tags, ".flac", library_root=root)
    _add_track(fid, dest, **_TAGS)

    out = organize_folder(fid)

    assert out["skipped"] == 1
    assert out["moved"] == 0
    assert dest.exists()


def test_organize_conflict_reported_not_overwritten(folder):
    fid, root = folder
    src1 = root / "loose1" / "a.flac"
    src2 = root / "loose2" / "b.flac"
    t1 = _add_track(fid, src1, **_TAGS)
    _add_track(fid, src2, **_TAGS)
    src1.write_bytes(b"FIRST")
    src2.write_bytes(b"SECOND")
    dest = _canonical(fid, t1, root)

    out = organize_folder(fid)

    assert out["moved"] == 1
    assert any("conflict" in e for e in out["errors"])
    assert dest.read_bytes() == b"FIRST"   # winner not clobbered by the loser


def test_track_to_tags_carries_totals():
    # Regression: _track_to_tags dropped track_total (and the pipeline never
    # stored disc_total), so organize misfiled multi-disc albums / rendered
    # {tracktotal} as 0.
    t = Track(path="/x.flac", track_num=3, track_total=10, disc_num=1, disc_total=2)
    tags = _track_to_tags(t)
    assert tags.track_total == 10
    assert tags.disc_total == 2


def test_organize_multidisc_uses_disc_folder(folder):
    fid, root = folder
    src = root / "loose" / "x.flac"
    tid = _add_track(
        fid, src, title="Song", artist="Artist", album="Album",
        album_artist="Artist", track_num=1, track_total=10, disc_num=1, disc_total=2,
    )

    out = organize_folder(fid)

    assert out["moved"] == 1
    with session() as s:
        newpath = s.get(Track, tid).path
    assert "Disc 1" in newpath   # disc_total=2 carried through → multi-disc folder


def test_upsert_track_persists_totals(tmp_path):
    from dragontag.app.ingest.pipeline import _upsert_track
    from dragontag.app.tagging.schema import TrackTags

    dest = tmp_path / "a.flac"
    tags = TrackTags(title="S", track=2, track_total=11, disc=1, disc_total=3)
    with session() as s:
        row = _upsert_track(s, dest, tags, tmp_path)
        tid = row.id
    try:
        with session() as s:
            row = s.get(Track, tid)
            assert row.track_total == 11
            assert row.disc_total == 3
    finally:
        with session() as s:
            row = s.get(Track, tid)
            if row:
                s.delete(row)
                s.commit()


def test_organize_db_failure_rolls_file_back(folder, monkeypatch):
    fid, root = folder
    src = root / "loose" / "whatever.flac"
    tid = _add_track(fid, src, **_TAGS)
    dest = _canonical(fid, tid, root)

    def _boom(self):
        raise RuntimeError("db down")
    monkeypatch.setattr(sqlmodel.Session, "commit", _boom)

    out = organize_folder(fid)
    monkeypatch.undo()

    assert out["moved"] == 0
    assert any("rolled back" in e for e in out["errors"])
    assert src.exists() and not dest.exists()   # compensating move restored it
    with session() as s:
        assert s.get(Track, tid).path == str(src)   # DB never advanced
