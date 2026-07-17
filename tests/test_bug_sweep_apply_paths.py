"""Manual apply paths must honor the pipeline's schema guarantees and audit trail.

* ``prepare_tags`` (extracted from ``_finalize_and_commit``) fills the
  mandatory RELEASETYPE and the RELEASESTATUS default; the review-apply
  routes call ``_commit_tag_path`` directly and used to skip both.
* ``POST /library/tracks/{id}/apply-match`` is a full destructive rewrite —
  it must snapshot + record a ``FileChange`` (revertable, visible in
  /changes) and carry the file's embedded lyrics/advisory across the
  canonical tag wipe instead of destroying them.
"""
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.ingest import pipeline
from dragontag.app.main import app, require_auth
from dragontag.app.models import FileChange, Job, JobStatus, ReviewReason, Track
from dragontag.app.tagging.schema import TrackTags


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


# ---- prepare_tags: shared schema guarantees ----

def test_prepare_tags_infers_releasetype_and_status():
    tags = TrackTags(title="t", track_total=1)
    pipeline.prepare_tags(None, tags)
    assert tags.release_type == "Single"
    assert tags.release_status == "Official"


def test_prepare_tags_keeps_explicit_values():
    tags = TrackTags(title="t", release_type="EP", release_status="Bootleg")
    pipeline.prepare_tags(None, tags)
    assert tags.release_type == "EP"
    assert tags.release_status == "Bootleg"


# ---- review_apply: routes through prepare_tags before _commit_tag_path ----

def _wait_for(cond, timeout: float = 10.0) -> None:
    """Poll until ``cond()`` — the review-apply commit runs in a background
    task since the request thread stopped doing the slow work."""
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if cond():
            return
        _time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _review_job(src: Path) -> int:
    with session() as s:
        j = Job(
            source_path=str(src), original_name=src.name,
            status=JobStatus.needs_review,
        )
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def test_review_apply_fills_mandatory_fields(client, tmp_path, monkeypatch):
    src = tmp_path / "song.wav"
    _make_wav(src)
    jid = _review_job(src)

    from dragontag.app.identify import musicbrainz as mbq
    monkeypatch.setattr(
        mbq, "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(title="T", track_total=12),
    )
    captured: dict = {}

    def fake_commit(s, job, src_path, tags, *, score):
        captured["tags"] = tags

    monkeypatch.setattr(pipeline, "_commit_tag_path", fake_commit)

    r = client.post(f"/review/{jid}/apply", data={"pick": "rec-id|rel-id"})
    assert r.status_code == 303
    _wait_for(lambda: "tags" in captured)  # commit runs in a background task now
    assert captured["tags"].release_type == "Album"       # inferred from 12 tracks
    assert captured["tags"].release_status == "Official"  # default applied


def test_review_apply_override_beats_inference(client, tmp_path, monkeypatch):
    src = tmp_path / "song2.wav"
    _make_wav(src)
    jid = _review_job(src)

    from dragontag.app.identify import musicbrainz as mbq
    monkeypatch.setattr(
        mbq, "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(title="T", track_total=12),
    )
    captured: dict = {}
    monkeypatch.setattr(
        pipeline, "_commit_tag_path",
        lambda s, job, src_path, tags, *, score: captured.update(tags=tags),
    )

    r = client.post(
        f"/review/{jid}/apply",
        data={"pick": "rec-id|rel-id", "release_type_override": "EP"},
    )
    assert r.status_code == 303
    _wait_for(lambda: "tags" in captured)  # commit runs in a background task now
    assert captured["tags"].release_type == "EP"


# ---- cover-art fetch failure routes to review (not a pipeline crash) ----

def test_commit_routes_to_review_on_cover_fetch_failure(tmp_path, monkeypatch):
    """A transient CAA fetch failure (5xx/SSL) must not abort ingest: the job is
    parked in needs_review (retriable) and the source file is left untouched."""
    import requests
    from dragontag.app import net

    src = tmp_path / "song.wav"
    _make_wav(src)
    jid = _review_job(src)

    def boom(url, **kw):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(net.requests, "get", boom)

    tags = TrackTags(title="T", artist_display="A", mb_album_id="rel-id")
    with session() as s:
        job = s.get(Job, jid)
        pipeline._commit_tag_path(s, job, src, tags, score=0.9)  # must not raise

    assert src.exists()  # file was neither tagged nor moved
    with session() as s:
        job = s.get(Job, jid)
        assert job.status == JobStatus.needs_review
        assert job.review_reason == ReviewReason.cover_fetch_failed


# ---- apply-match: snapshot + FileChange + lyrics preserved ----

def test_apply_match_records_change_and_keeps_lyrics(client, tmp_path, monkeypatch):
    from dragontag.app.tagging.partial import read_lyrics, write_lyrics

    p = tmp_path / "track.wav"
    _make_wav(p)
    # Give the file pre-existing tags + embedded lyrics/advisory.
    from dragontag.app.tagging.partial import write_basic_tags
    write_basic_tags(
        p, title="Old Title", artist="Old Artist", album=None, album_artist=None,
        track=None, track_total=None, disc=None, disc_total=None,
    )
    write_lyrics(p, "la la la", 1)

    with session() as s:
        t = Track(path=str(p), title="Old Title", artist="Old Artist", has_lyrics=True)
        s.add(t)
        s.commit()
        s.refresh(t)
        tid = t.id

    from dragontag.app.identify import musicbrainz as mbq
    from dragontag.app.tagging import coverart
    monkeypatch.setattr(
        mbq, "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(
            title="New Title", artist_display="New Artist",
            mb_track_id="rec-id", mb_album_id="rel-id",
        ),
    )
    monkeypatch.setattr(coverart, "fetch_for_release", lambda mbid: None)

    r = client.post(f"/library/tracks/{tid}/apply-match", data={"pick": "rec-id|rel-id"})
    assert r.status_code == 303

    # Lyrics survived the canonical rewrite.
    assert read_lyrics(p) == "la la la"
    # Audit row exists at the file's location with the pre-write snapshot.
    with session() as s:
        change = s.exec(
            select(FileChange).where(FileChange.file_path == str(p))
        ).first()
        assert change is not None
        assert change.job_id is None
        assert change.original_tags_json.get("tags", {}).get("TIT2") == ["Old Title"]
        # DB row still knows the file has lyrics.
        assert s.get(Track, tid).has_lyrics is True

    # And the change is actually revertable back to the old tags.
    from dragontag.app.library.revert import revert_change
    ok, _msg = revert_change(change.id)
    assert ok
    from mutagen.wave import WAVE
    assert WAVE(str(p)).tags.getall("TIT2")[0].text == ["Old Title"]
