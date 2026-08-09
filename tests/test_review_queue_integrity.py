"""Review cards preserve metadata, artwork choices, and duplicate overrides."""
from __future__ import annotations

import json
import time
import uuid
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app import main as main_mod
from dragontag.app.db import session
from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.ingest import pipeline
from dragontag.app.main import app, require_auth
from dragontag.app.models import LibraryFolder, Job, JobStatus, ReviewReason, Track
from dragontag.app.tagging.coverart import CoverArt
from dragontag.app.tagging.schema import TrackTags


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _review_job(tmp_path: Path, candidates: list[dict]) -> int:
    token = uuid.uuid4().hex
    source = tmp_path / f"{token}.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)
    with session() as s:
        job = Job(
            source_path=str(source),
            original_name=source.name,
            status=JobStatus.needs_review,
            candidates_json={"items": candidates},
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def _candidate(rec: str, rel: str, *, title="Caroline", artist="Aminé", album="Good for You"):
    return {
        "recording_id": rec,
        "release_id": rel,
        "score": 0.95,
        "title": title,
        "artist": artist,
        "album": album,
    }


def _wait_for(captured: dict, count: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(captured) >= count:
            return
        time.sleep(0.02)
    raise AssertionError(f"only captured {len(captured)}/{count} applies")


def test_queue_renders_metadata_and_one_cover_per_release(client, tmp_path):
    token = uuid.uuid4().hex
    release = f"release-{token}"
    job_id = _review_job(tmp_path, [
        _candidate(f"rec-a-{token}", release),
        _candidate(f"rec-b-{token}", release, title="Caroline (clean)"),
    ])

    response = client.get("/queue")
    assert response.status_code == 200
    assert "Caroline" in response.text
    assert "Aminé" in response.text
    assert "Good for You" in response.text
    assert response.text.count(f"/release/{release}/front-250") == 1
    assert f'id="cover_release_{job_id}"' in response.text
    assert f'id="cover_release_explicit_{job_id}"' in response.text
    assert "bulk.querySelectorAll('input[name=job_ids]:checked')" not in response.text
    assert "input[name=job_ids][form=review-bulk-form]:checked" in response.text


def test_single_and_bulk_apply_ignore_stale_default_and_embed_selected_release_art(
    client, tmp_path, monkeypatch
):
    token = uuid.uuid4().hex
    stale_single_rel = f"stale-single-{token}"
    stale_bulk_rel_a = f"stale-bulk-a-{token}"
    stale_bulk_rel_b = f"stale-bulk-b-{token}"
    single_rel = f"single-{token}"
    bulk_rel_a = f"bulk-a-{token}"
    bulk_rel_b = f"bulk-b-{token}"
    single = _review_job(tmp_path, [
        _candidate(f"old-rec-s-{token}", stale_single_rel),
        _candidate(f"rec-s-{token}", single_rel),
    ])
    bulk_a = _review_job(tmp_path, [
        _candidate(f"old-rec-a-{token}", stale_bulk_rel_a),
        _candidate(f"rec-a-{token}", bulk_rel_a),
    ])
    bulk_b = _review_job(tmp_path, [
        _candidate(f"old-rec-b-{token}", stale_bulk_rel_b),
        _candidate(f"rec-b-{token}", bulk_rel_b),
    ])
    expected = {
        single_rel: (b"single-jpeg", "image/jpeg"),
        bulk_rel_a: (b"bulk-a-png", "image/png"),
        bulk_rel_b: (b"bulk-b-jpeg", "image/jpeg"),
    }
    fetched: list[str] = []
    captured: dict[int, tuple[bytes, str]] = {}

    monkeypatch.setattr(
        mbq,
        "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(
            title="Caroline",
            artist_display="Aminé",
            artists=["Aminé"],
            album="Good for You",
            album_artist_display="Aminé",
            album_artists=["Aminé"],
            mb_track_id=recording_id,
            mb_album_id=release_id,
            release_type="Album",
        ),
    )

    def fake_cover(release_id):
        fetched.append(release_id)
        data, mime = expected[release_id]
        return CoverArt(data=data, mime=mime, width=1000, height=1000)

    def fake_commit(s, job, src, tags, *, score):
        captured[job.id] = (tags.cover_bytes, tags.cover_mime)
        job.status = JobStatus.done
        s.add(job)
        s.commit()

    from dragontag.app.tagging import coverart
    monkeypatch.setattr(coverart, "fetch_for_release", fake_cover)
    monkeypatch.setattr(pipeline, "_commit_tag_path", fake_commit)

    response = client.post(
        f"/review/{single}/apply",
        data={
            "pick": f"rec-s-{token}|{single_rel}",
            # Original/top-candidate form default. Without an explicit marker,
            # artwork must follow the newly selected tagging release.
            "cover_release_id": stale_single_rel,
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200

    response = client.post(
        "/review/bulk-apply",
        data={
            "job_ids": [str(bulk_a), str(bulk_b)],
            f"pick_{bulk_a}": f"rec-a-{token}|{bulk_rel_a}",
            f"cover_{bulk_a}": stale_bulk_rel_a,
            f"pick_{bulk_b}": f"rec-b-{token}|{bulk_rel_b}",
            f"cover_{bulk_b}": stale_bulk_rel_b,
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger"])
    assert set(trigger["reviewApplied"]["ids"]) == {bulk_a, bulk_b}

    _wait_for(captured, 3)
    assert captured[single] == expected[single_rel]
    assert captured[bulk_a] == expected[bulk_rel_a]
    assert captured[bulk_b] == expected[bulk_rel_b]
    assert set(fetched) == {single_rel, bulk_rel_a, bulk_rel_b}


def test_review_apply_honors_explicit_alternate_cover_release(client, tmp_path, monkeypatch):
    token = uuid.uuid4().hex
    tagging_rel = f"tagging-{token}"
    alternate_rel = f"alternate-{token}"
    job_id = _review_job(tmp_path, [
        _candidate(f"rec-{token}", tagging_rel),
        _candidate(f"other-rec-{token}", alternate_rel),
    ])
    captured: dict[int, bytes] = {}

    monkeypatch.setattr(
        mbq,
        "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(
            title="Caroline",
            artist_display="AminÃ©",
            artists=["AminÃ©"],
            album="Good for You",
            mb_track_id=recording_id,
            mb_album_id=release_id,
            release_type="Album",
        ),
    )

    from dragontag.app.tagging import coverart
    monkeypatch.setattr(
        coverart,
        "fetch_for_release",
        lambda release_id: CoverArt(
            data=f"cover:{release_id}".encode(), mime="image/jpeg", width=1000, height=1000
        ),
    )

    def fake_commit(s, job, src, tags, *, score):
        captured[job.id] = tags.cover_bytes
        job.status = JobStatus.done
        s.add(job)
        s.commit()

    monkeypatch.setattr(pipeline, "_commit_tag_path", fake_commit)

    response = client.post(
        f"/review/{job_id}/apply",
        data={
            "pick": f"rec-{token}|{tagging_rel}",
            "cover_release_id": alternate_rel,
            "cover_release_explicit": "1",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    _wait_for(captured, 1)
    assert captured[job_id] == f"cover:{alternate_rel}".encode()


def test_first_duplicate_apply_keeps_card_second_queues_override(
    client, tmp_path, monkeypatch
):
    token = uuid.uuid4().hex
    recording = f"rec-{token}"
    release = f"rel-{token}"
    job_id = _review_job(tmp_path, [_candidate(recording, release)])
    with session() as s:
        folder = s.exec(
            select(LibraryFolder)
            .where(LibraryFolder.enabled == True)  # noqa: E712
            .order_by(LibraryFolder.priority, LibraryFolder.id)
        ).first()
        duplicate_path = Path(folder.path) / "Aminé" / "Good for You" / f"{token}.flac"
        s.add(Track(
            path=str(duplicate_path),
            library_folder_id=folder.id,
            mb_track_id=recording,
            artist="Aminé",
            title="Caroline",
            duration=1.0,
        ))
        s.commit()

    queued: list[tuple] = []
    monkeypatch.setattr(
        main_mod.tasks,
        "run_chain",
        lambda *args, **kwargs: queued.append((args, kwargs)) or 123,
    )

    first = client.post(
        f"/review/{job_id}/apply",
        data={"pick": f"{recording}|{release}"},
        headers={"HX-Request": "true"},
    )
    assert first.status_code == 204
    assert queued == []
    with session() as s:
        job = s.get(Job, job_id)
        assert job.review_reason == ReviewReason.duplicate_detected
        assert job.candidates_json["duplicates"] == [str(duplicate_path)]

    page = client.get("/queue")
    assert "Possible duplicate already in this library" in page.text
    assert "Apply anyway" in page.text
    assert str(duplicate_path) in page.text

    second = client.post(
        f"/review/{job_id}/apply",
        data={"pick": f"{recording}|{release}"},
        headers={"HX-Request": "true"},
    )
    assert second.status_code == 200
    assert len(queued) == 1
