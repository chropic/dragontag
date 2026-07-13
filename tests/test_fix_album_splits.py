"""fix_album_splits: elect a canonical release per release group and re-unify
every track onto it (full MB re-tag), with an offline majority-vote fallback
for groups that carry no MusicBrainz ids. All MB/CAA access is monkeypatched.
"""
import wave
from pathlib import Path

import pytest
from mutagen.wave import WAVE
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.actions import (
    BATCH_NUCLEAR,
    LIBRARY_ACTIONS,
    _elect_canonical_release,
    _group_is_split,
    fix_album_splits,
)
from dragontag.app.models import LibraryFolder, Track
from dragontag.app.tagging.schema import TrackTags


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


# Two editions of one release group: the 2-track standard edition and the
# 3-track deluxe that contains every recording (mirrors the real-world case
# of tracks scattered across "Album" and "Album (Deluxe)").
REL_SMALL = {
    "id": "rel-small", "title": "Alive", "status": "Official",
    "release-group": {"id": "rg-1"},
    "medium-list": [{"position": 1, "format": "Digital Media", "track-count": 2,
                     "track-list": [
                         {"position": 1, "recording": {"id": "rec-1"}},
                         {"position": 2, "recording": {"id": "rec-2"}},
                     ]}],
}
REL_BIG = {
    "id": "rel-big", "title": "Alive (Deluxe)", "status": "Official",
    "release-group": {"id": "rg-1"},
    "medium-list": [{"position": 1, "format": "Digital Media", "track-count": 3,
                     "track-list": [
                         {"position": 1, "recording": {"id": "rec-1"}},
                         {"position": 2, "recording": {"id": "rec-2"}},
                         {"position": 3, "recording": {"id": "rec-3"}},
                     ]}],
}
RELEASES = {r["id"]: r for r in (REL_SMALL, REL_BIG)}


def _fake_fetch_release(release_id):
    return RELEASES[release_id]


def _fake_assemble(*, release_id, recording_id, rel=None):
    rel = rel or RELEASES[release_id]
    pos = next(
        int(t["position"]) for m in rel["medium-list"]
        for t in m["track-list"] if t["recording"]["id"] == recording_id
    )
    return TrackTags(
        title=f"Track {pos}", artist_display="Artist", artists=["Artist"],
        album=rel["title"], album_artist_display="Artist",
        album_artists=["Artist"],
        track=pos, track_total=rel["medium-list"][0]["track-count"],
        disc=1, disc_total=1, media="Digital Media",
        release_type="Album", release_status="Official",
        mb_track_id=recording_id, mb_album_id=release_id,
        mb_release_group_id="rg-1",
        release_track_total=rel["medium-list"][0]["track-count"],
    )


@pytest.fixture()
def mb_stub(monkeypatch):
    from dragontag.app.identify import musicbrainz as mbq
    from dragontag.app.tagging import coverart
    monkeypatch.setattr(mbq, "fetch_release", _fake_fetch_release)
    monkeypatch.setattr(mbq, "assemble_tags", _fake_assemble)
    monkeypatch.setattr(coverart, "fetch_for_release", lambda _rid: None)


def test_group_is_split_detection():
    def t(**kw):
        return Track(path="/x", **kw)
    same = [t(mb_album_id="a", album="X", album_artist="Y", track_total=10)] * 2
    assert not _group_is_split(same)
    assert _group_is_split([t(mb_album_id="a"), t(mb_album_id="b")])
    assert _group_is_split([t(album="X", album_artist="Y"), t(album="X2", album_artist="Y")])
    assert _group_is_split([t(album="X", album_artist="Y", track_total=20),
                            t(album="X", album_artist="Y", track_total=29)])


def test_election_prefers_coverage(mb_stub):
    rid, rel, recs = _elect_canonical_release(
        {"rel-small", "rel-big"}, {"rec-1", "rec-2", "rec-3"}
    )
    assert rid == "rel-big"
    assert recs == {"rec-1", "rec-2", "rec-3"}
    # Equal coverage: bigger edition, then lexicographic id.
    rid, _, _ = _elect_canonical_release({"rel-small", "rel-big"}, {"rec-1"})
    assert rid == "rel-big"


def test_split_album_unified_onto_canonical_release(folder, mb_stub):
    fid, root = folder
    _add_track(fid, root / "Artist" / "Alive" / "01. Track 1.wav",
               title="Track 1", album="Alive", album_artist="Artist",
               track_total=2, mb_track_id="rec-1", mb_album_id="rel-small",
               mb_release_group_id="rg-1")
    _add_track(fid, root / "Artist" / "Alive (Deluxe)" / "02. Track 2.wav",
               title="Track 2", album="Alive (Deluxe)", album_artist="Artist",
               track_total=3, mb_track_id="rec-2", mb_album_id="rel-big",
               mb_release_group_id="rg-1")

    summary = fix_album_splits(fid)
    assert summary["groups_fixed"] == 1
    assert summary["tracks_retagged"] == 2

    with session() as s:
        rows = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
    assert {t.mb_album_id for t in rows} == {"rel-big"}
    assert {t.album for t in rows} == {"Alive (Deluxe)"}
    assert {t.track_total for t in rows} == {3}
    # Files physically merged into the canonical album folder.
    parents = {Path(t.path).parent.name for t in rows}
    assert len(parents) == 1
    # Tags on disk match too (WAV shares the ID3 writer with MP3).
    for t in rows:
        id3 = WAVE(t.path).tags
        assert str(id3["TALB"].text[0]) == "Alive (Deluxe)"
        assert str(id3["TPE2"].text[0]) == "Artist"
        assert id3["TXXX:MUSICBRAINZ_ALBUMID"].text[0] == "rel-big"


def test_bonus_track_not_on_canonical_release_left_alone(folder, mb_stub, monkeypatch):
    fid, root = folder
    # Force the small edition to win so rec-3 has no slot on it.
    monkeypatch.setattr(
        "dragontag.app.library.actions._elect_canonical_release",
        lambda ids, recs, ctx=None: (
            "rel-small", REL_SMALL, {"rec-1", "rec-2"}
        ),
    )
    _add_track(fid, root / "A" / "Alive" / "01.wav",
               title="Track 1", album="Alive", album_artist="Artist",
               mb_track_id="rec-1", mb_album_id="rel-small", mb_release_group_id="rg-1")
    bonus = _add_track(fid, root / "A" / "Alive (Deluxe)" / "03.wav",
                       title="Track 3", album="Alive (Deluxe)", album_artist="Artist",
                       mb_track_id="rec-3", mb_album_id="rel-big", mb_release_group_id="rg-1")

    summary = fix_album_splits(fid)
    assert summary["skipped_bonus"] == 1
    with session() as s:
        t = s.get(Track, bonus)
        assert t.mb_album_id == "rel-big"          # untouched
        assert Path(t.path).name == "03.wav"       # not moved


def test_offline_fallback_votes_album_pair(folder, mb_stub):
    fid, root = folder
    _add_track(fid, root / "B" / "Same Album" / "01.wav",
               album="Same Album", album_artist="B")
    _add_track(fid, root / "B" / "Same Album" / "02.wav",
               album="Same Album", album_artist="B")
    # Edition suffixes are folded by the normalized grouping key, so the
    # "(Deluxe)" variant lands in the same group and loses the vote 2:1.
    odd = _add_track(fid, root / "B" / "Same Album (Deluxe)" / "03.wav",
                     album="Same Album (Deluxe)", album_artist="B")

    summary = fix_album_splits(fid)
    assert summary["tracks_voted"] == 1
    with session() as s:
        t = s.get(Track, odd)
        assert t.album == "Same Album"
        assert Path(t.path).parent.name == "Same Album"


def test_protected_tracks_are_skipped(folder, mb_stub):
    fid, root = folder
    _add_track(fid, root / "C" / "X" / "01.wav",
               album="X", album_artist="C", protected=True,
               mb_track_id="rec-1", mb_album_id="rel-small", mb_release_group_id="rg-1")
    _add_track(fid, root / "C" / "X Deluxe" / "02.wav",
               album="X Deluxe", album_artist="C", protected=True,
               mb_track_id="rec-2", mb_album_id="rel-big", mb_release_group_id="rg-1")
    summary = fix_album_splits(fid)
    assert summary["groups_fixed"] == 0
    assert summary["tracks_retagged"] == 0


def test_registry_and_nuclear_chain_wiring():
    assert "fix_album_splits" in LIBRARY_ACTIONS
    label, desc, fn = LIBRARY_ACTIONS["fix_album_splits"]
    assert fn is fix_album_splits
    assert "fix_album_splits" in BATCH_NUCLEAR
    # Must run before the downstream cleanup passes that assume unified albums.
    assert BATCH_NUCLEAR.index("fix_album_splits") < BATCH_NUCLEAR.index("fix_disc_folders")
