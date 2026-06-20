"""Per-track edit menu: manual tag edit, protect-from-overwrite toggle,
MB/AcoustID apply-match, and single-track LRCLIB lyrics fetch."""
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.config import settings, store
from dragontag.app.db import session
from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.main import app, require_auth
from dragontag.app.models import LibraryFolder, Track


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


@pytest.fixture()
def track(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="test")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    p = tmp_path / "song.flac"
    p.write_bytes(b"\x00audio")
    with session() as s:
        t = Track(
            library_folder_id=fid, path=str(p),
            title="Old Title", artist="Old Artist", album="Old Album",
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        tid = t.id
    yield tid, p
    with session() as s:
        row = s.get(Track, tid)
        if row:
            s.delete(row)
        folder_row = s.get(LibraryFolder, fid)
        if folder_row:
            s.delete(folder_row)
        s.commit()


def test_edit_modal_renders(client, track):
    tid, _ = track
    resp = client.get(f"/library/tracks/{tid}/edit")
    assert resp.status_code == 200
    assert b"Edit track" in resp.content


def test_edit_save_updates_partial_tags_only(client, track, monkeypatch):
    tid, p = track
    calls = {}

    def fake_write_basic_tags(path, **fields):
        calls["path"] = path
        calls["fields"] = fields

    monkeypatch.setattr(
        "dragontag.app.tagging.partial.write_basic_tags", fake_write_basic_tags
    )
    resp = client.post(
        f"/library/tracks/{tid}/edit",
        data={"title": "New Title", "artist": "New Artist", "album": "", "track_num": "3"},
    )
    assert resp.status_code == 303
    assert calls["path"] == p
    assert calls["fields"]["title"] == "New Title"
    assert calls["fields"]["artist"] == "New Artist"
    assert calls["fields"]["album"] is None
    assert calls["fields"]["track"] == 3

    with session() as s:
        row = s.get(Track, tid)
        assert row.title == "New Title"
        assert row.artist == "New Artist"
        assert row.album is None
        assert row.track_num == 3


def test_protect_toggle_sets_flag_and_exclude_list(client, track):
    tid, p = track
    resp = client.post(f"/library/tracks/{tid}/protect")
    assert resp.status_code == 303
    with session() as s:
        row = s.get(Track, tid)
        assert row.protected is True
    assert str(p) in settings().scan_exclude_files

    resp = client.post(f"/library/tracks/{tid}/protect")
    assert resp.status_code == 303
    with session() as s:
        row = s.get(Track, tid)
        assert row.protected is False
    assert str(p) not in settings().scan_exclude_files


def test_apply_match_writes_full_tags_and_upserts_track(client, track, monkeypatch):
    tid, p = track

    fake_tags = types.SimpleNamespace(
        title="MB Title", artist="MB Artist", mb_album_id=None,
        cover_bytes=None, cover_mime=None,
    )

    def fake_assemble_tags(*, release_id, recording_id):
        assert recording_id == "rec1"
        assert release_id == "rel1"
        return fake_tags

    written = {}

    def fake_write_tags(path, tags):
        written["path"] = path
        written["tags"] = tags

    upserted = {}

    def fake_upsert(s, dest, tags, lib_root):
        upserted["dest"] = dest
        upserted["tags"] = tags

    monkeypatch.setattr(mbq, "assemble_tags", fake_assemble_tags)
    monkeypatch.setattr("dragontag.app.tagging.writers.write_tags", fake_write_tags)
    monkeypatch.setattr("dragontag.app.ingest.pipeline._upsert_track", fake_upsert)

    resp = client.post(f"/library/tracks/{tid}/apply-match", data={"pick": "rec1|rel1"})
    assert resp.status_code == 303
    assert written["path"] == p
    assert written["tags"] is fake_tags
    assert upserted["dest"] == p


def test_apply_match_without_pick_is_error_toast(client, track):
    tid, _ = track
    resp = client.post(f"/library/tracks/{tid}/apply-match", data={})
    assert resp.status_code == 303
    assert "error" in resp.headers["HX-Trigger"]


def test_fetch_lyrics_writes_and_updates_track(client, track, monkeypatch):
    tid, p = track

    monkeypatch.setattr(
        "dragontag.app.tagging.lyrics_fetcher.fetch",
        lambda *, artist, title, album: "[00:01.00]la la la",
    )
    monkeypatch.setattr("dragontag.app.tagging.advisory.is_explicit", lambda text: False)
    written = {}
    monkeypatch.setattr(
        "dragontag.app.tagging.partial.write_lyrics",
        lambda path, lyrics, advisory: written.update(path=path, lyrics=lyrics, advisory=advisory),
    )

    resp = client.post(f"/library/tracks/{tid}/fetch-lyrics")
    assert resp.status_code == 303
    assert written["path"] == p
    assert written["advisory"] == 0

    with session() as s:
        row = s.get(Track, tid)
        assert row.has_lyrics is True
        assert row.advisory == 0
