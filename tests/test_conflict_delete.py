"""resolve_conflict action=delete: the incoming duplicate is quarantined to
.dragontag-trash (never unlinked), the FileChange audit row follows it so
revert still works, and the job ends as skipped."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.config import env, settings, store
from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import FileChange, Job, JobStatus, ReviewReason


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def _conflict_job(src: Path, dest: Path) -> int:
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"incoming audio")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"library copy")
    with session() as s:
        j = Job(
            source_path=str(src), original_name=src.name, kind="ingest",
            status=JobStatus.needs_review,
            review_reason=ReviewReason.destination_conflict,
            destination_path=str(dest),
        )
        s.add(j)
        s.commit()
        s.refresh(j)
        c = FileChange(
            job_id=j.id, file_path=str(src), original_path=str(src),
            original_name=src.name, original_tags_json={}, new_tags_json={},
        )
        s.add(c)
        s.commit()
        return j.id


def test_delete_quarantines_incoming_file(client, tmp_path):
    lib = env().library_path
    src = tmp_path / "drop" / "dupe.flac"
    (tmp_path / "drop" / "dupe.lrc").parent.mkdir(parents=True, exist_ok=True)
    dest = lib / "Artist" / "Album" / "01. Song.flac"
    jid = _conflict_job(src, dest)
    (tmp_path / "drop" / "dupe.lrc").write_bytes(b"lyrics")

    resp = client.post(f"/review/{jid}/resolve_conflict", data={"action": "delete"})

    assert resp.status_code == 303
    assert not src.exists()                       # incoming file left the drop
    assert dest.read_bytes() == b"library copy"   # library copy untouched
    trash = lib / ".dragontag-trash"
    trashed = list(trash.rglob("dupe.flac"))
    assert len(trashed) == 1
    assert trashed[0].read_bytes() == b"incoming audio"
    assert list(trash.rglob("dupe.lrc"))          # sidecar followed

    with session() as s:
        job = s.get(Job, jid)
        assert job.status == JobStatus.skipped
        assert "trash" in (job.log or "")
        # Audit row re-pointed at the trash copy so revert/move-back work.
        change = s.exec(select(FileChange).where(FileChange.job_id == jid)).first()
        assert change.file_path == str(trashed[0])
        s.delete(change)
        s.delete(job)
        s.commit()

    # Trash root excluded from future scans.
    assert str(trash) in settings().scan_exclude_dirs

    def _cleanup(cur):
        return {"scan_exclude_dirs": [d for d in cur.scan_exclude_dirs if d != str(trash)]}
    store().transact(_cleanup)


def test_delete_on_resolved_job_is_rejected(client, tmp_path):
    src = tmp_path / "gone.flac"
    dest = env().library_path / "X" / "gone.flac"
    jid = _conflict_job(src, dest)
    with session() as s:
        j = s.get(Job, jid)
        j.status = JobStatus.done
        s.add(j)
        s.commit()

    resp = client.post(f"/review/{jid}/resolve_conflict", data={"action": "delete"})

    assert resp.status_code == 303
    assert "error" in resp.headers.get("HX-Trigger", "")
    assert src.exists()  # nothing moved
