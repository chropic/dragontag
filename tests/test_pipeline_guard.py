"""S1: a job sitting in needs_review must not be raced by a second job for
the same source path, and ``process()`` must refuse to run a job that isn't
in a processable state (e.g. one already resolved by another thread).
"""
from pathlib import Path

from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.ingest import pipeline
from dragontag.app.models import Job, JobStatus


def test_enqueue_dedups_against_needs_review(tmp_path):
    p = tmp_path / "song.flac"
    p.write_bytes(b"\x00")
    with session() as s:
        existing = Job(source_path=str(p), original_name=p.name, status=JobStatus.needs_review)
        s.add(existing)
        s.commit()
        s.refresh(existing)
        existing_id = existing.id

    job = pipeline.enqueue(p)

    assert job.id == existing_id
    with session() as s:
        rows = s.exec(select(Job).where(Job.source_path == str(p))).all()
        assert len(rows) == 1


def test_process_skips_job_not_in_processable_state(tmp_path):
    p = tmp_path / "song.flac"
    p.write_bytes(b"\x00")
    with session() as s:
        job = Job(source_path=str(p), original_name=p.name, status=JobStatus.needs_review)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    pipeline.process(job_id)

    with session() as s:
        unchanged = s.get(Job, job_id)
        assert unchanged.status == JobStatus.needs_review
