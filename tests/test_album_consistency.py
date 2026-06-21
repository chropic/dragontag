"""Album/folder consistency checker: majority-vote normalize + physical move."""
import wave
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.actions import check_album_consistency
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


def _add_track(fid: int, path: Path, **kw) -> int:
    _make_wav(path)
    with session() as s:
        t = Track(library_folder_id=fid, path=str(path), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def test_majority_album_wins_and_outliers_move_by_mb_release_group(folder):
    fid, root = folder
    _add_track(fid, root / "ArtistA" / "Correct Album" / "01.wav",
               album="Correct Album", album_artist="ArtistA",
               mb_release_group_id="rg-1")
    _add_track(fid, root / "ArtistA" / "Correct Album" / "02.wav",
               album="Correct Album", album_artist="ArtistA",
               mb_release_group_id="rg-1")
    t3 = _add_track(fid, root / "ArtistA" / "Old Album Name" / "03.wav",
                     album="Old Album Name", album_artist="ArtistA",
                     mb_release_group_id="rg-1")

    out = check_album_consistency(fid)

    assert out["groups_checked"] == 1
    assert out["tracks_fixed"] == 1
    with session() as s:
        moved = s.get(Track, t3)
        assert moved.album == "Correct Album"
        assert Path(moved.path).parent.name == "Correct Album"
        assert Path(moved.path).exists()
    assert not (root / "ArtistA" / "Old Album Name").exists()


def test_normalized_fallback_when_no_mb_id(folder):
    fid, root = folder
    _add_track(fid, root / "Artist" / "Album (Deluxe)" / "01.wav",
               album="Album (Deluxe)", album_artist="Artist")
    _add_track(fid, root / "Artist" / "album" / "02.wav",
               album="album", album_artist="artist")

    out = check_album_consistency(fid)

    assert out["groups_checked"] == 1
    assert out["tracks_fixed"] == 1


def test_protected_tracks_skipped_entirely(folder):
    fid, root = folder
    _add_track(fid, root / "Artist" / "Correct" / "01.wav",
               album="Correct", album_artist="Artist", mb_release_group_id="rg-2")
    protected_id = _add_track(fid, root / "Artist" / "Wrong" / "02.wav",
                               album="Wrong", album_artist="Artist",
                               mb_release_group_id="rg-2", protected=True)

    check_album_consistency(fid)

    with session() as s:
        p = s.get(Track, protected_id)
        assert p.album == "Wrong"
        assert Path(p.path).parent.name == "Wrong"
        assert Path(p.path).exists()


def test_idempotent_second_run_is_a_noop(folder):
    fid, root = folder
    _add_track(fid, root / "ArtistA" / "Correct Album" / "01.wav",
               album="Correct Album", album_artist="ArtistA",
               mb_release_group_id="rg-3")
    _add_track(fid, root / "ArtistA" / "Correct Album" / "02.wav",
               album="Correct Album", album_artist="ArtistA",
               mb_release_group_id="rg-3")
    _add_track(fid, root / "ArtistA" / "Old Album Name" / "03.wav",
               album="Old Album Name", album_artist="ArtistA",
               mb_release_group_id="rg-3")

    check_album_consistency(fid)
    out2 = check_album_consistency(fid)

    assert out2["tracks_fixed"] == 0
