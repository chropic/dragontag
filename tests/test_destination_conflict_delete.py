"""Destination-conflict discard behavior.

Skip + Delete is intentionally the only review action that removes incoming
audio. These tests keep the existing library destination in view so a future
refactor cannot accidentally delete or overwrite the wrong side of a conflict.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import (
    FileChange,
    Job,
    JobStatus,
    ReviewDraft,
    ReviewReason,
    Track,
)


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _conflict_job(source: Path, destination: Path) -> int:
    with session() as s:
        job = Job(
            source_path=str(source),
            original_name=source.name,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.destination_conflict,
            destination_path=str(destination),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def test_skip_delete_removes_only_incoming_file_and_sidecar(client, tmp_path):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"incoming")
    sidecar = source.with_suffix(".lrc")
    sidecar.write_text("incoming lyrics", encoding="utf-8")
    destination = tmp_path / "library" / "song.flac"
    destination.parent.mkdir()
    destination.write_bytes(b"library original")
    job_id = _conflict_job(source, destination)

    with session() as s:
        indexed = Track(path=str(source), title="Incoming")
        s.add(indexed)
        s.flush()
        job = s.get(Job, job_id)
        job.track_id = indexed.id
        s.add(job)
        s.add(ReviewDraft(job_id=job_id, fields_json={"title": "draft"}))
        change = FileChange(job_id=job_id, file_path=str(source), original_path=str(source))
        s.add(change)
        s.commit()
        s.refresh(indexed)
        s.refresh(change)
        track_id = indexed.id
        change_id = change.id

    response = client.post(
        f"/review/{job_id}/resolve_conflict", data={"action": "skip_delete"}
    )

    assert response.status_code == 303
    assert not source.exists()
    assert not sidecar.exists()
    assert destination.read_bytes() == b"library original"
    with session() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.skipped
        assert "Incoming destination-conflict file deleted" in job.log
        assert job.track_id is None
        assert s.get(Track, track_id) is None
        assert s.get(ReviewDraft, job_id) is None
        # Keep the tag-write audit trail even though its file is intentionally
        # gone; deleting the row would hide that a destructive write happened.
        assert s.get(FileChange, change_id) is not None


def test_skip_delete_refuses_same_source_and_destination(client, tmp_path):
    source = tmp_path / "same.flac"
    source.write_bytes(b"keep me")
    job_id = _conflict_job(source, source)

    response = client.post(
        f"/review/{job_id}/resolve_conflict", data={"action": "skip_delete"}
    )

    assert response.status_code == 303
    assert "error" in response.headers["HX-Trigger"]
    assert source.read_bytes() == b"keep me"
    with session() as s:
        assert s.get(Job, job_id).status == JobStatus.needs_review


def test_skip_delete_reports_database_divergence(client, tmp_path, monkeypatch):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"incoming")
    destination = tmp_path / "song.flac"
    destination.write_bytes(b"existing")
    job_id = _conflict_job(source, destination)

    def fail_commit(_self):
        raise OSError("db offline")

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = client.post(
        f"/review/{job_id}/resolve_conflict", data={"action": "skip_delete"}
    )

    assert response.status_code == 303
    assert "error" in response.headers["HX-Trigger"]
    assert not source.exists()  # physical truth is surfaced, never disguised
    assert destination.read_bytes() == b"existing"
    with session() as s:
        assert s.get(Job, job_id).status == JobStatus.needs_review


def test_conflict_route_rejects_unknown_action_instead_of_renaming(client, tmp_path):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"incoming")
    destination = tmp_path / "song.flac"
    destination.write_bytes(b"existing")
    job_id = _conflict_job(source, destination)

    response = client.post(
        f"/review/{job_id}/resolve_conflict", data={"action": "typo"}
    )

    assert response.status_code == 400
    assert source.read_bytes() == b"incoming"
    assert destination.read_bytes() == b"existing"


def test_skip_delete_is_not_available_to_other_review_reasons(client, tmp_path):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"incoming")
    destination = tmp_path / "preview.flac"
    with session() as s:
        job = Job(
            source_path=str(source),
            original_name=source.name,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.dry_run,
            destination_path=str(destination),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    response = client.post(
        f"/review/{job_id}/resolve_conflict", data={"action": "skip_delete"}
    )

    assert response.status_code == 400
    assert source.read_bytes() == b"incoming"


def test_destination_conflict_card_renders_confirmed_skip_delete(client, tmp_path):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"incoming")
    destination = tmp_path / "song.flac"
    destination.write_bytes(b"existing")
    _conflict_job(source, destination)

    response = client.get("/queue")

    assert response.status_code == 200
    assert 'value="skip_delete"' in response.text
    assert "Skip + Delete" in response.text
    assert "This cannot be undone" in response.text
