"""Resolving a destination conflict moves an already-tagged file into the
library — the route must index it (Track row) and move the lyric sidecar,
exactly like the pipeline's normal happy path, or the file stays invisible
until a manual rescan.
"""
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus, LibraryFolder, ReviewReason, Track


@pytest.fixture()
def client():
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
        w.writeframes(b"\x00\x00" * 100)


def test_rename_resolution_creates_track_row_and_moves_sidecar(client, tmp_path):
    with session() as s:
        folder = LibraryFolder(path=str(tmp_path), label="lib")
        s.add(folder)
        s.commit()
        s.refresh(folder)
        fid = folder.id

    src = tmp_path / "drop" / "song.wav"
    _make_wav(src)
    src.with_suffix(".lrc").write_text("[00:01.00]la", encoding="utf-8")
    dest = tmp_path / "Artist" / "Album" / "01. Song.wav"
    _make_wav(dest)  # pre-existing file → the conflict

    with session() as s:
        job = Job(
            source_path=str(src), original_name=src.name,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.destination_conflict,
            destination_path=str(dest),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    try:
        resp = client.post(f"/review/{job_id}/resolve_conflict", data={"action": "rename"})
        assert resp.status_code == 303

        with session() as s:
            row = s.get(Job, job_id)
            assert row.status == JobStatus.done
            final = Path(row.destination_path)
            assert final != dest and final.exists()
            # The moved file was indexed and linked to the job.
            track = s.exec(select(Track).where(Track.path == str(final))).first()
            assert track is not None
            assert row.track_id == track.id
            # Sidecar followed the audio.
            assert final.with_suffix(".lrc").exists()
            assert not src.with_suffix(".lrc").exists()
    finally:
        with session() as s:
            for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all():
                s.delete(t)
            j = s.get(Job, job_id)
            if j:
                s.delete(j)
            f = s.get(LibraryFolder, fid)
            if f:
                s.delete(f)
            s.commit()
