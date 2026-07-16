"""The Completions page: live gap queries (lyrics/untagged/duplicates/tag
problems), snapshot sections (missing tracks via IncompleteAlbum, covers +
genres via HealthItem written by scan_health), section fragments, dismiss."""
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library import actions
from dragontag.app.main import app, require_auth
from dragontag.app.models import HealthItem, IncompleteAlbum, LibraryFolder, Track


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="health-test")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    yield fid, tmp_path
    with session() as s:
        for model in (Track, HealthItem, IncompleteAlbum):
            col = getattr(model, "library_folder_id")
            for row in s.exec(select(model).where(col == fid)).all():
                s.delete(row)
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
        w.writeframes(b"\x00\x00" * 50)


def _track(fid, path: Path, **kw):
    with session() as s:
        t = Track(library_folder_id=fid, path=str(path), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def test_page_renders_with_summary_and_sections(client, folder):
    fid, root = folder
    _track(fid, root / "a.flac", title="Alpha", artist="A", album_artist="A",
           has_lyrics=False, mb_track_id=None, mb_album_id=None)

    resp = client.get("/completions")

    assert resp.status_code == 200
    for name in ("missing-tracks", "duplicates", "no-lyrics", "covers",
                 "genres", "untagged", "tag-problems"):
        assert f"/completions/section/{name}" in resp.text


def test_no_lyrics_and_untagged_sections(client, folder):
    fid, root = folder
    _track(fid, root / "with.flac", title="HasLyrics", artist="A", album_artist="A",
           has_lyrics=True, mb_track_id="r1", mb_album_id="rel1")
    _track(fid, root / "without.flac", title="NoLyricsSong", artist="A",
           album_artist="A", has_lyrics=False, mb_track_id="r2", mb_album_id="rel2")
    _track(fid, root / "untagged.flac", title="UntaggedSong", artist="A",
           album_artist="A", has_lyrics=True)

    no_lyrics = client.get("/completions/section/no-lyrics").text
    assert "NoLyricsSong" in no_lyrics
    assert "HasLyrics" not in no_lyrics
    assert "fetch lyrics" in no_lyrics

    untagged = client.get("/completions/section/untagged").text
    assert "UntaggedSong" in untagged
    assert "NoLyricsSong" not in untagged


def test_duplicates_section_groups_and_duration_gate(client, folder):
    fid, root = folder
    # Same MB recording id -> duplicate group regardless of tags.
    _track(fid, root / "d1.flac", title="Dup", artist="A", mb_track_id="same-rec", duration=100.0)
    _track(fid, root / "d2.flac", title="Dup", artist="A", mb_track_id="same-rec", duration=100.0)
    # Same artist/title but durations 100 vs 250 -> NOT a duplicate.
    _track(fid, root / "l1.flac", title="LiveTake", artist="B", duration=100.0)
    _track(fid, root / "l2.flac", title="LiveTake", artist="B", duration=250.0)

    body = client.get("/completions/section/duplicates").text
    assert "d1.flac" in body and "d2.flac" in body
    assert "LiveTake" not in body


def test_tag_problems_section(client, folder):
    fid, root = folder
    _track(fid, root / "broken.flac", title=None, artist="A", album_artist="A")

    body = client.get("/completions/section/tag-problems").text
    assert "missing title" in body
    assert "broken.flac" in body


def test_scan_health_snapshot_and_dismiss(client, folder):
    fid, root = folder
    # Track with no genre and no art anywhere -> both categories.
    p1 = root / "Artist" / "Album" / "01.wav"
    _make_wav(p1)
    _track(fid, p1, title="Bare", artist="A", album="Album", album_artist="A")
    # Track whose album dir has a cover.jpg -> genre finding only.
    p2 = root / "Artist" / "Covered" / "01.wav"
    _make_wav(p2)
    (p2.parent / "cover.jpg").write_bytes(b"img")
    _track(fid, p2, title="Covered", artist="A", album="Covered", album_artist="A")

    out = actions.scan_health(fid)
    assert out["missing_genre"] == 2
    assert out["missing_cover"] == 1

    covers = client.get("/completions/section/covers").text
    assert "01.wav" in covers and "Covered" not in covers
    genres = client.get("/completions/section/genres").text
    assert "Covered" in genres

    # Delete-then-insert: fixing the cover and rescanning drops the stale row.
    (p1.parent / "cover.jpg").write_bytes(b"img")
    out = actions.scan_health(fid)
    assert out["missing_cover"] == 0
    with session() as s:
        rows = s.exec(select(HealthItem).where(
            HealthItem.library_folder_id == fid, HealthItem.category == "missing_cover"
        )).all()
    assert rows == []

    # Dismiss removes a single genre finding.
    with session() as s:
        row = s.exec(select(HealthItem).where(
            HealthItem.library_folder_id == fid, HealthItem.category == "missing_genre"
        )).first()
    resp = client.post(f"/completions/item/{row.id}/delete")
    assert resp.status_code == 303
    with session() as s:
        assert s.get(HealthItem, row.id) is None


def test_missing_tracks_section_uses_incomplete_albums(client, folder):
    fid, _root = folder
    with session() as s:
        s.add(IncompleteAlbum(
            library_folder_id=fid, mb_album_id="mb-1", album="Halfbum",
            artist="A", local_count=3, expected_count=10,
            missing_titles_json=["1-04. Ghost"],
        ))
        s.commit()

    body = client.get("/completions/section/missing-tracks").text
    assert "Halfbum" in body
    assert "1-04. Ghost" in body
    assert "musicbrainz.org/release/mb-1" in body


def test_unknown_section_is_404(client):
    assert client.get("/completions/section/nope").status_code == 404


def test_actions_keep_their_old_output_shapes(folder):
    fid, root = folder
    p = root / "x.flac"
    p.write_bytes(b"a")
    _track(fid, p, title="T", artist="A", album_artist="A")

    dup = actions.find_duplicates(fid)
    assert set(dup) == {"groups", "files"}
    val = actions.validate_tags(fid)
    assert set(val) == {"checked", "problems"}
