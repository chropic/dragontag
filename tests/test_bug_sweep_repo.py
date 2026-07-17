"""Regression bundle for the 2026-07-09 repo-wide bug sweep.

One test per fixed bug, grouped by area:

* scoring: explicit ``"artist": null`` in an MB credit must not crash
* pipeline/conflict: a blocked move still records a revertible FileChange
* resolve_conflict: status guard, path_lock, honest failure toast, audit re-point
* retag-selected: needs_review tracks are actually requeued
* revert: the Track row is fully re-synced (numbering, advisory, lyrics)
* job log endpoint: HTML-escaped
* incomplete-albums pagination: search query urlencoded
* uploads: one failed stream doesn't drop the batch
* organize route: batch guard applies
* watcher: stop() ends the settle thread
* library actions: file mutations hold path_lock
"""
import asyncio
import threading
import wave
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import FileChange, IncompleteAlbum, Job, JobStatus, ReviewReason, Track


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


@contextmanager
def _recording_lock(record: list):
    def fake_path_lock(path):
        @contextmanager
        def _cm():
            record.append(Path(path))
            yield
        return _cm()
    yield fake_path_lock


# ---------------------------------------------------------------------------
# scoring: null artist credit
# ---------------------------------------------------------------------------


def test_score_candidate_tolerates_null_artist_credit():
    from dragontag.app.identify.scoring import score_candidate

    sb = score_candidate(
        candidate_recording={
            "title": "Song",
            "artist-credit": [{"artist": None, "name": "X"}],
        },
        candidate_release={"title": "Album"},
        clues={"title": "Song", "artist": "X", "album": "Album", "duration": None},
    )
    assert sb.artist == pytest.approx(1.0)  # fell back to the credit's own name


# ---------------------------------------------------------------------------
# pipeline conflict branch + resolve_conflict
# ---------------------------------------------------------------------------


def _run_conflicting_ingest(tmp_path, monkeypatch):
    """Drive a real ingest into a destination conflict; return (job_id, src, dest)."""
    from dragontag.app.config import env
    from dragontag.app.ingest import pipeline
    from dragontag.app.tagging import lyrics_fetcher
    from dragontag.app.tagging.schema import TrackTags

    src = tmp_path / "song.wav"
    _make_wav(src)

    monkeypatch.setattr(
        pipeline.existing_tags,
        "read",
        lambda _: {"mb_track_id": "rec-1", "mb_album_id": "rel-1", "duration": 1.0},
    )
    monkeypatch.setattr(
        pipeline.mbq,
        "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(
            title="T", artists=["A"], artist_display="A",
            album="Al", album_artist_display="A", track=1,
        ),
    )
    monkeypatch.setattr(lyrics_fetcher, "fetch", lambda **kw: None)

    # Occupy the canonical destination so the move conflicts.
    dest = env().library_path / "A" / "Al" / "01. T.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"occupied")

    job = pipeline.enqueue(src, dry_run=False)
    pipeline.process(job.id)
    return job.id, src, dest


def test_conflict_records_revertible_filechange(tmp_path, monkeypatch):
    job_id, src, dest = _run_conflicting_ingest(tmp_path, monkeypatch)

    with session() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.needs_review
        assert job.review_reason == ReviewReason.destination_conflict
        change = s.exec(select(FileChange).where(FileChange.job_id == job_id)).first()
        # The in-place tag write happened even though the move was blocked —
        # it must be auditable/revertible at the file's real location.
        assert change is not None
        assert change.file_path == str(src)
        assert change.original_path == str(src)


def test_resolve_conflict_rename_repoints_filechange(tmp_path, monkeypatch, client):
    job_id, src, dest = _run_conflicting_ingest(tmp_path, monkeypatch)

    resp = client.post(f"/review/{job_id}/resolve_conflict", data={"action": "rename"})
    assert resp.status_code == 303

    renamed = dest.with_stem(dest.stem + "-1")
    assert renamed.exists()
    assert not src.exists()
    with session() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.done
        assert job.destination_path == str(renamed)
        change = s.exec(select(FileChange).where(FileChange.job_id == job_id)).first()
        assert change.file_path == str(renamed)  # audit row followed the file


def test_resolve_conflict_guards_non_review_jobs(client, tmp_path):
    p = tmp_path / "done.flac"
    p.write_bytes(b"\x00")
    with session() as s:
        job = Job(
            source_path=str(p), original_name=p.name,
            status=JobStatus.done, destination_path=str(tmp_path / "x.flac"),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    resp = client.post(f"/review/{job_id}/resolve_conflict", data={"action": "replace"})
    assert resp.status_code == 303
    assert "error" in resp.headers["HX-Trigger"]
    with session() as s:
        assert s.get(Job, job_id).status == JobStatus.done  # untouched


def test_resolve_conflict_reports_failed_move(client, tmp_path, monkeypatch):
    from dragontag.app import main as main_mod
    from dragontag.app.library.mover import MoveResult

    p = tmp_path / "stuck.flac"
    p.write_bytes(b"\x00")
    dest = tmp_path / "lib" / "stuck.flac"
    with session() as s:
        job = Job(
            source_path=str(p), original_name=p.name,
            status=JobStatus.needs_review,
            review_reason=ReviewReason.destination_conflict,
            destination_path=str(dest),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    monkeypatch.setattr(
        main_mod, "move",
        lambda src, dst, overwrite=False: MoveResult(moved=False, destination=dst, conflict=True),
    )
    resp = client.post(f"/review/{job_id}/resolve_conflict", data={"action": "rename"})
    assert resp.status_code == 303
    assert "error" in resp.headers["HX-Trigger"]  # not a silent redirect
    with session() as s:
        assert s.get(Job, job_id).status == JobStatus.needs_review


# ---------------------------------------------------------------------------
# retag-selected requeues stuck reviews
# ---------------------------------------------------------------------------


def test_retag_selected_requeues_needs_review_track(client, tmp_path, monkeypatch):
    from dragontag.app import main as main_mod

    monkeypatch.setattr(main_mod.pipeline, "submit", lambda job_id: None)

    p = tmp_path / "stuck-review.flac"
    p.write_bytes(b"\x00")
    with session() as s:
        track = Track(path=str(p), title="T")
        stuck = Job(
            source_path=str(p), original_name=p.name,
            status=JobStatus.needs_review, review_reason=ReviewReason.low_score,
        )
        s.add(track)
        s.add(stuck)
        s.commit()
        s.refresh(track)
        s.refresh(stuck)
        track_id, stuck_id = track.id, stuck.id

    resp = client.post("/library/retag-selected", data={"track_ids": str(track_id)})
    assert resp.status_code == 303
    with session() as s:
        row = s.get(Job, stuck_id)
        assert row.status == JobStatus.queued  # reset, not silently skipped
        assert row.review_reason is None


# ---------------------------------------------------------------------------
# revert fully re-syncs the Track row
# ---------------------------------------------------------------------------


def test_refresh_track_syncs_numbering_advisory_and_lyrics(tmp_path, monkeypatch):
    from dragontag.app.library import revert

    p = tmp_path / "reverted.flac"
    p.write_bytes(b"\x00")
    monkeypatch.setattr(
        revert.existing_tags,
        "read",
        lambda _: {
            "title": "Old", "artist": "A", "album": "Al", "album_artist": "AA",
            "track": "03/12", "disc": "2/2", "disc_total": None,
            "advisory": 0, "has_lyrics": False,
            "mb_track_id": "r", "mb_album_id": "l", "mb_release_group_id": "g",
        },
    )
    with session() as s:
        track = Track(
            path=str(p), title="New", track_num=7, track_total=8,
            disc_num=1, disc_total=1, advisory=1, has_lyrics=True,
        )
        s.add(track)
        s.commit()
        s.refresh(track)
        track_id = track.id

    with session() as s:
        revert._refresh_track(s, p)
        s.commit()

    with session() as s:
        row = s.get(Track, track_id)
        assert (row.track_num, row.track_total) == (3, 12)
        assert (row.disc_num, row.disc_total) == (2, 2)
        assert row.advisory == 0
        assert row.has_lyrics is False
        assert row.mb_release_group_id == "g"


# ---------------------------------------------------------------------------
# web layer
# ---------------------------------------------------------------------------


def test_job_log_endpoint_escapes_html(client):
    with session() as s:
        job = Job(
            source_path="x", original_name="x",
            status=JobStatus.done, log='<script>alert("xss")</script>',
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    resp = client.get(f"/jobs/{job_id}/log")
    assert resp.status_code == 200
    assert "<script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_incomplete_redirects_to_completions(client):
    # The old Incomplete tab lives on the Completions page now; the rows
    # render via the missing-tracks section fragment (tested in
    # test_completions_page.py).
    resp = client.get("/library/incomplete", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"].startswith("/completions")


# ---------------------------------------------------------------------------
# uploads: per-file isolation
# ---------------------------------------------------------------------------


class _FakeUpload:
    def __init__(self, filename, chunks=None, fail=False):
        self.filename = filename
        self.content_type = "audio/flac"
        self._chunks = list(chunks or [])
        self._fail = fail
        self._served_first = False

    async def read(self, n):
        if self._fail:
            if not self._served_first:
                self._served_first = True
                return b"\x00" * 10
            raise OSError("client went away")
        return self._chunks.pop(0) if self._chunks else b""


def test_upload_stream_failure_does_not_drop_batch(monkeypatch):
    from dragontag.app.config import env
    from dragontag.app.ingest import uploads

    class _FakeJob:
        id = 12345

    monkeypatch.setattr(uploads.pipeline, "enqueue", lambda p, **kw: _FakeJob())
    monkeypatch.setattr(uploads.pipeline, "submit", lambda job_id: None)

    bad = _FakeUpload("broken.flac", fail=True)
    good = _FakeUpload("fine.flac", chunks=[b"\x01" * 10])
    job_ids, errors = asyncio.run(uploads.save_uploads([bad, good]))

    assert job_ids == [12345]  # the good file still made it through
    assert len(errors) == 1 and "broken.flac" in errors[0]
    # No truncated partial left behind in the watched drop folder.
    assert not list(env().drop_path.glob("broken*"))


# ---------------------------------------------------------------------------
# organize route: batch guard
# ---------------------------------------------------------------------------


def test_organize_route_refuses_while_task_running(client, monkeypatch):
    from dragontag.app import main as main_mod
    from dragontag.app.models import LibraryFolder

    calls = []
    monkeypatch.setattr(main_mod.tasks, "run_task", lambda *a, **kw: calls.append(a) or 1)

    with session() as s:
        folder = s.exec(select(LibraryFolder)).first()
        running = Job(source_path="", original_name="bg", kind="scan", status=JobStatus.running)
        s.add(running)
        s.commit()
        s.refresh(running)
        run_id, folder_id = running.id, folder.id

    try:
        resp = client.post("/library/organize", data={"folder_id": str(folder_id)})
        assert resp.status_code == 303
        assert "error" in resp.headers["HX-Trigger"]
        assert calls == []  # no second file-moving task was started
    finally:
        with session() as s:
            obj = s.get(Job, run_id)
            if obj:
                s.delete(obj)
            s.commit()


# ---------------------------------------------------------------------------
# watcher: settle thread stops
# ---------------------------------------------------------------------------


def test_watcher_settle_loop_exits_on_stop():
    from dragontag.app.ingest.watcher import _Handler

    h = _Handler()
    t = threading.Thread(target=h.settle_loop, daemon=True)
    t.start()
    h._stopped.set()
    h._has_pending.set()  # wake the wait() so the loop notices immediately
    t.join(timeout=2)
    assert not t.is_alive()


# ---------------------------------------------------------------------------
# library actions hold path_lock
# ---------------------------------------------------------------------------


def _make_folder_with_track(tmp_path, filename):
    from dragontag.app.models import LibraryFolder

    p = tmp_path / filename
    _make_wav(p)
    with session() as s:
        folder = LibraryFolder(path=str(tmp_path), label="lock-test")
        s.add(folder)
        s.commit()
        s.refresh(folder)
        track = Track(path=str(p), library_folder_id=folder.id, title="T")
        s.add(track)
        s.commit()
        s.refresh(track)
        return folder.id, track.id, p


def test_tag_advisories_holds_path_lock(tmp_path, monkeypatch):
    from dragontag.app.library import actions

    folder_id, _track_id, p = _make_folder_with_track(tmp_path, "adv.wav")
    locked: list[Path] = []
    with _recording_lock(locked) as fake:
        monkeypatch.setattr(actions.filelock, "path_lock", fake)
        actions.tag_advisories_for_folder(folder_id)
    assert p in locked


