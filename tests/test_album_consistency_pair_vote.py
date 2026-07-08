"""The album/album-artist majority must be voted as a joint pair — two
independent votes could combine one track's album with another track's artist
into a state no track ever had, then rewrite/move the whole group to it.
"""
import wave
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.actions import _majority_pair, check_album_consistency
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


def _row(album, artist, indexed_offset=0):
    return Track(
        path=f"/x/{album}-{artist}.wav", album=album, album_artist=artist,
        indexed_at=datetime(2026, 1, 1) + timedelta(days=indexed_offset),
    )


def test_pair_vote_never_invents_a_combination():
    # Independent votes: album "X" (2 votes) + artist "A" (2 votes) → (X, A),
    # a pair carried by NO track. The joint vote must pick a real pair.
    tracks = [
        _row("X", "B"), _row("X", "C"),
        _row("Y", "A"), _row("Z", "A"),
        _row("W", "D"),
    ]
    winner = _majority_pair(tracks)
    assert winner in {(t.album, t.album_artist) for t in tracks}


def test_pair_vote_majority_wins():
    tracks = [_row("X", "A"), _row("X", "A"), _row("Y", "B")]
    assert _majority_pair(tracks) == ("X", "A")


def test_pair_vote_tie_prefers_most_recently_indexed():
    tracks = [_row("X", "A", 0), _row("Y", "B", 5)]
    assert _majority_pair(tracks) == ("Y", "B")


def test_consistency_run_normalizes_to_a_real_pair(folder):
    fid, root = folder
    combos = [("X", "B"), ("X", "C"), ("Y", "A"), ("Z", "A"), ("W", "D")]
    ids = []
    with session() as s:
        for i, (album, artist) in enumerate(combos):
            p = root / artist / album / f"{i:02d}.wav"
            _make_wav(p)
            t = Track(
                library_folder_id=fid, path=str(p), title=f"T{i}",
                album=album, album_artist=artist, mb_release_group_id="rg-1",
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            ids.append(t.id)

    check_album_consistency(fid)

    with session() as s:
        rows = [s.get(Track, i) for i in ids]
        finals = {(r.album, r.album_artist) for r in rows}
        # Everything converged onto exactly one pair, and it's a pair that
        # actually existed before the run.
        assert len(finals) == 1
        assert finals.pop() in set(combos)
