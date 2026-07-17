"""Review-queue per-item actions: X (skip) and manual-tag apply.

Both answer htmx (in-place card removal + toast) and plain form posts. The
manual-apply path builds a TrackTags from hand-entered fields and runs the same
background commit as an MB match.
"""
import time
import types

import pytest
from fastapi.testclient import TestClient

from dragontag.app.db import session
from dragontag.app.identify import musicbrainz as mbq  # noqa: F401 (kept parallel to other suites)
from dragontag.app.ingest import pipeline as pipeline_mod
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _review_job() -> int:
    with session() as s:
        j = Job(source_path="/tmp/song.flac", original_name="song.flac",
                status=JobStatus.needs_review)
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


# ---- X / skip ----

def test_skip_marks_job_skipped_htmx(client):
    jid = _review_job()
    r = client.post(f"/review/{jid}/skip", headers={"HX-Request": "true"})
    assert r.status_code == 200  # empty body → htmx swaps the card out
    assert "showToast" in r.headers.get("HX-Trigger", "")
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.skipped


def test_skip_non_review_is_error_toast(client):
    with session() as s:
        j = Job(source_path="/tmp/x.flac", original_name="x.flac", status=JobStatus.done)
        s.add(j)
        s.commit()
        s.refresh(j)
        jid = j.id
    r = client.post(f"/review/{jid}/skip", headers={"HX-Request": "true"})
    assert r.status_code == 204  # htmx does not swap on 204 → card stays
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.done  # untouched


# ---- manual-apply ----

@pytest.fixture()
def stub_commit(monkeypatch):
    captured: dict = {}

    def fake_commit(s, job, src, tags, *, score):
        captured["tags"] = tags
        job.status = JobStatus.done
        s.add(job)
        s.commit()

    monkeypatch.setattr(pipeline_mod, "_commit_tag_path", fake_commit)
    return captured


def _wait(captured, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "tags" in captured:
            return
        time.sleep(0.02)
    raise AssertionError("manual commit never captured tags")


def test_manual_apply_builds_tags_and_commits(client, stub_commit):
    jid = _review_job()
    r = client.post(
        f"/review/{jid}/manual-apply",
        data={"title": "My Song", "artist": "My Band", "album": "My Album",
              "track_num": "3", "track_total": "10"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    _wait(stub_commit)
    tags = stub_commit["tags"]
    assert tags.title == "My Song"
    assert tags.artist_display == "My Band"
    assert tags.album == "My Album"
    assert tags.album_artist_display == "My Band"  # falls back to artist
    assert tags.track == 3 and tags.track_total == 10


def test_single_apply_htmx_removes_card_and_backgrounds(client, stub_commit, monkeypatch):
    """htmx single apply returns an empty 200 (card swapped out) + toast and
    commits in the background rather than a blocking 303 redirect."""
    from dragontag.app.tagging.schema import TrackTags
    monkeypatch.setattr(
        mbq, "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(title="T", track_total=1),
    )
    jid = _review_job()
    r = client.post(
        f"/review/{jid}/apply",
        data={"pick": "rec-id|rel-id"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert r.text == ""  # empty body → outerHTML swap removes the card
    assert "showToast" in r.headers.get("HX-Trigger", "")
    _wait(stub_commit)
    assert stub_commit["tags"].title == "T"


def test_manual_apply_requires_title_and_artist(client, stub_commit):
    jid = _review_job()
    r = client.post(
        f"/review/{jid}/manual-apply",
        data={"title": "", "artist": "Band"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 204  # validation error toast, no swap
    assert "tags" not in stub_commit
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.needs_review  # still in review
