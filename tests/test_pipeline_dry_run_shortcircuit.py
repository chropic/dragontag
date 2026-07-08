"""The MBID short-circuit must run through the same finalize step as the
search path: honor dry-run (no write/move), infer a missing RELEASETYPE, and
default RELEASESTATUS — a dry-run bulk re-tag over an already-tagged library
takes the short-circuit for every file, so bypassing the gate there silently
rewrote the whole library.

Also covers ``enqueue(requeue_reviews=True)``: an explicit bulk re-tag must
reset a stuck needs_review job back to queued instead of silently counting a
no-op as "queued".
"""
import wave
from pathlib import Path

from dragontag.app.db import session
from dragontag.app.ingest import pipeline
from dragontag.app.models import Job, JobStatus, ReviewReason
from dragontag.app.tagging.schema import TrackTags


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_mbid_shortcircuit_honors_dry_run_and_finalizes(tmp_path, monkeypatch):
    p = tmp_path / "song.wav"
    _make_wav(p)
    original_bytes = p.read_bytes()

    monkeypatch.setattr(
        pipeline.existing_tags,
        "read",
        lambda _: {"mb_track_id": "rec-1", "mb_album_id": "rel-1", "duration": 1.0},
    )
    assembled = {}

    def fake_assemble(*, release_id, recording_id):
        assembled["ids"] = (recording_id, release_id)
        # No release_type / release_status from MB — finalize must fill both.
        return TrackTags(title="T", artists=["A"], album="Al", track_total=10)

    monkeypatch.setattr(pipeline.mbq, "assemble_tags", fake_assemble)

    job = pipeline.enqueue(p, dry_run=True)
    pipeline.process(job.id)

    with session() as s:
        row = s.get(Job, job.id)
        assert row.status == JobStatus.needs_review
        assert row.review_reason == ReviewReason.dry_run
        assert assembled["ids"] == ("rec-1", "rel-1")
        # Finalize defaults were applied even on the short-circuit path.
        assert row.chosen_tags_json["release_type"] == "Album"
        assert row.chosen_tags_json["release_status"] == "Official"
        assert row.destination_path

    # Dry run: the file was neither rewritten nor moved.
    assert p.exists()
    assert p.read_bytes() == original_bytes


def test_enqueue_requeue_reviews_resets_stuck_job(tmp_path):
    p = tmp_path / "song.flac"
    p.write_bytes(b"\x00")
    with session() as s:
        stuck = Job(
            source_path=str(p), original_name=p.name,
            status=JobStatus.needs_review, review_reason=ReviewReason.low_score,
        )
        s.add(stuck)
        s.commit()
        s.refresh(stuck)
        stuck_id = stuck.id

    job = pipeline.enqueue(p, requeue_reviews=True)

    assert job.id == stuck_id
    with session() as s:
        row = s.get(Job, stuck_id)
        assert row.status == JobStatus.queued
        assert row.review_reason is None
