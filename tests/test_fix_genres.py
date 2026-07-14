"""fix_genres_for_folder: backfill genres for tracks that have none.

Only empty genres are filled (never overwritten); the recording is tried first
and the release-group is the fallback; tracks without a MusicBrainz id are left
alone. MusicBrainz is monkeypatched, so no network is used.
"""
import wave
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.library.actions import fix_genres_for_folder
from dragontag.app.models import LibraryFolder, Track
from dragontag.app.tagging.partial import read_genre, write_genre

_REC_TAGS = {
    "rec-rock": [{"name": "rock", "count": "9"}],
    "rec-empty": [],  # forces the release-group fallback
}
_RG_TAGS = {"rg-pop": [{"name": "pop", "count": "5"}]}


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


def _add_track(fid: int, path: Path, **kw) -> None:
    _make_wav(path)
    with session() as s:
        s.add(Track(library_folder_id=fid, path=str(path), **kw))
        s.commit()


def test_backfills_only_empty_genres(folder, monkeypatch):
    fid, root = folder
    monkeypatch.setattr(mbq, "fetch_recording", lambda rid: {"tag-list": _REC_TAGS.get(rid, [])})
    monkeypatch.setattr(mbq, "fetch_release_group", lambda rg: {"tag-list": _RG_TAGS.get(rg, [])})

    p_fill = root / "a.wav"           # recording has tags -> filled
    p_keep = root / "b.wav"           # already has a genre -> untouched
    p_fallback = root / "c.wav"       # recording empty -> release-group fallback
    p_nombid = root / "d.wav"         # no MB id -> not eligible

    _add_track(fid, p_fill, mb_track_id="rec-rock")
    _add_track(fid, p_keep, mb_track_id="rec-rock")
    _add_track(fid, p_fallback, mb_track_id="rec-empty", mb_release_group_id="rg-pop")
    _add_track(fid, p_nombid)
    write_genre(p_keep, ["Metal"])    # pre-existing genre to protect

    out = fix_genres_for_folder(fid)

    assert read_genre(p_fill) == ["Rock"]
    assert read_genre(p_keep) == ["Metal"]      # never overwritten
    assert read_genre(p_fallback) == ["Pop"]    # came from the release-group
    assert read_genre(p_nombid) == []           # skipped: no MB id
    assert out == {"processed": 3, "tagged": 2}
