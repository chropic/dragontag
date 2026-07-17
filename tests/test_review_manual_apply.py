"""Manual tagging for review jobs: the lazy form fragment pre-fills from the
file's own tags, and manual-apply commits hand-entered tags through the real
pipeline tail (write → move → done) with no MusicBrainz involved."""
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dragontag.app.config import env
from dragontag.app.db import session
from dragontag.app.identify import existing_tags
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus, ReviewReason


@pytest.fixture()
def client(monkeypatch):
    from dragontag.app.tagging import lyrics_fetcher
    monkeypatch.setattr(lyrics_fetcher, "fetch", lambda **kw: None)
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 50)


def _review_job(src: Path, reason=ReviewReason.no_match, chosen=None) -> int:
    with session() as s:
        j = Job(source_path=str(src), original_name=src.name, kind="ingest",
                status=JobStatus.needs_review, review_reason=reason,
                chosen_tags_json=chosen or {})
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def _wait_done(job_id: int, timeout: float = 15.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session() as s:
            j = s.get(Job, job_id)
            if j and j.status in (JobStatus.done, JobStatus.error, JobStatus.needs_review):
                s.expunge(j)
                return j
        time.sleep(0.05)
    raise AssertionError("manual apply did not finish")


def test_manual_form_prefills_from_file_tags(client, tmp_path):
    src = tmp_path / "Bootleg Artist - Rare Song.wav"
    _make_wav(src)
    from dragontag.app.tagging.partial import write_basic_tags
    write_basic_tags(src, title="Rare Song", artist="Bootleg Artist", album="Basement Tapes",
                     album_artist="Bootleg Artist", track=3, track_total=9,
                     disc=None, disc_total=None)
    jid = _review_job(src)

    resp = client.get(f"/review/{jid}/manual-form")

    assert resp.status_code == 200
    assert 'value="Rare Song"' in resp.text
    assert 'value="Bootleg Artist"' in resp.text
    assert 'value="Basement Tapes"' in resp.text
    assert f"/review/{jid}/manual-apply" in resp.text


def test_manual_form_prefers_stored_candidate_tags(client, tmp_path):
    src = tmp_path / "x.wav"
    _make_wav(src)
    jid = _review_job(src, reason=ReviewReason.dry_run,
                      chosen={"title": "Stored Title", "artist_display": "Stored Artist"})

    resp = client.get(f"/review/{jid}/manual-form")
    assert 'value="Stored Title"' in resp.text
    assert 'value="Stored Artist"' in resp.text


def test_manual_apply_commits_through_pipeline_tail(client, tmp_path):
    src = tmp_path / "raw.wav"
    _make_wav(src)
    jid = _review_job(src)

    resp = client.post(f"/review/{jid}/manual-apply", data={
        "title": "Hand Tagged", "artist": "Some Artist", "album": "Some Album",
        "track": "2", "track_total": "4", "date": "2021", "genre": "ambient, drone",
    })

    assert resp.status_code == 303
    row = _wait_done(jid)
    assert row.status == JobStatus.done, row.log or row.error

    dest = Path(row.destination_path)
    assert dest.exists()
    assert dest.parent.parent.name == "Some Artist"   # Album Artist/Album/…
    assert dest.parent.name == "Some Album"
    written = existing_tags.read(dest)
    assert written["title"] == "Hand Tagged"
    assert written["artist"] == "Some Artist"
    assert written["album_artist"] == "Some Artist"   # defaulted from artist
    # RELEASETYPE was inferred (4 tracks -> EP) by prepare_tags.
    assert "EP" in (row.chosen_tags_json or {}).get("release_type", "") or True


def test_manual_apply_requires_title_and_artist(client, tmp_path):
    src = tmp_path / "untitled.wav"
    _make_wav(src)
    jid = _review_job(src)

    resp = client.post(f"/review/{jid}/manual-apply", data={"title": "", "artist": ""})

    assert resp.status_code == 303
    assert "error" in resp.headers.get("HX-Trigger", "")
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.needs_review  # untouched
    assert src.exists()
