"""Regression tests for the library bug sweep: rollback moves must check
``MoveResult`` (a conflict does not raise), the reaper must not kill quiet but
alive worker threads, and a failed upload stream must not leave a partial file
in the drop folder."""
import threading
import time
from datetime import timedelta
from pathlib import Path

from dragontag.app import tasks
from dragontag.app.db import session
from dragontag.app.library import organizer, revert
from dragontag.app.library.mover import MoveResult
from dragontag.app.models import FileChange, Job, JobStatus, LibraryFolder, Track
from dragontag.app.timeutil import now_utc


# ---- organizer: failed rollback (conflict) must surface as DIVERGED ----

def test_organize_rollback_conflict_reports_diverged(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "old" / "song.mp3"
    src.parent.mkdir()
    src.write_bytes(b"x")

    with session() as s:
        folder = LibraryFolder(path=str(lib))
        s.add(folder)
        s.commit()
        s.refresh(folder)
        track = Track(path=str(src), library_folder_id=folder.id, title="T", artist="A")
        s.add(track)
        s.commit()
        fid = folder.id

    dest = lib / "new" / "song.mp3"
    monkeypatch.setattr(organizer, "build_destination", lambda *a, **k: dest)

    calls = []

    def fake_move(a, b, overwrite=False):
        calls.append((a, b))
        if len(calls) == 1:
            return MoveResult(moved=True, destination=b, conflict=False)
        # Rollback: original location now occupied — conflict, no exception.
        return MoveResult(moved=False, destination=b, conflict=True)

    monkeypatch.setattr(organizer, "move", fake_move)

    class _BoomSession:
        def __enter__(self):
            raise RuntimeError("db down")
        def __exit__(self, *a):
            return False

    real_session = organizer.session
    state = {"n": 0}

    def flaky_session():
        state["n"] += 1
        # First session() call loads the folder/tracks; the second (the
        # per-track DB update) fails.
        return real_session() if state["n"] == 1 else _BoomSession()

    monkeypatch.setattr(organizer, "session", flaky_session)

    summary = organizer.organize_folder(fid)
    assert summary["diverged"], summary
    assert any("DIVERGED" in e for e in summary["errors"])
    assert not any("rolled back" in e for e in summary["errors"])


# ---- revert.move_back: failed rollback must not claim "restored" ----

def test_move_back_rollback_conflict_reports_divergence(tmp_path, monkeypatch):
    cur = tmp_path / "lib" / "song.mp3"
    cur.parent.mkdir(parents=True)
    cur.write_bytes(b"x")
    orig = tmp_path / "drop" / "song.mp3"
    orig.parent.mkdir(parents=True)

    with session() as s:
        change = FileChange(file_path=str(cur), original_path=str(orig))
        s.add(change)
        s.commit()
        s.refresh(change)
        cid = change.id

    def fake_move(a, b, overwrite=False):
        if str(b) == str(orig) or str(b).startswith(str(orig.parent)):
            return MoveResult(moved=True, destination=b, conflict=False)
        return MoveResult(moved=False, destination=b, conflict=True)

    # Forward move succeeds; DB commit fails; rollback move conflicts.
    monkeypatch.setattr(revert, "move", lambda a, b, overwrite=False: (
        MoveResult(moved=True, destination=b, conflict=False)
        if str(a) == str(cur)
        else MoveResult(moved=False, destination=b, conflict=True)
    ))

    import sqlmodel
    orig_commit = sqlmodel.Session.commit
    calls = {"n": 0}

    def failing_commit(self):
        calls["n"] += 1
        raise RuntimeError("db down")

    # Patch commit only around the move_back call's inner commit by counting:
    # the function commits once (after the forward move).
    monkeypatch.setattr(sqlmodel.Session, "commit", failing_commit)
    try:
        ok, msg = revert.move_back(cid)
    finally:
        monkeypatch.setattr(sqlmodel.Session, "commit", orig_commit)

    assert not ok
    assert "restored to its previous location" not in msg
    assert "could not be restored" in msg


# ---- reaper: quiet but alive worker thread is not reaped ----

def test_reaper_skips_job_with_live_thread():
    with session() as s:
        j = Job(source_path="", original_name="slow", kind="backup", status=JobStatus.running)
        j.updated_at = now_utc() - tasks.STALE_RUNNING_AFTER - timedelta(minutes=1)
        s.add(j)
        s.commit()
        s.refresh(j)
        jid = j.id

    stop = threading.Event()
    t = threading.Thread(target=stop.wait, daemon=True)
    t.start()
    with tasks._threads_lock:
        tasks._live_threads[jid] = t
    try:
        tasks.reap_stale_jobs()
        with session() as s:
            assert s.get(Job, jid).status == JobStatus.running
    finally:
        stop.set()
        t.join()
        with tasks._threads_lock:
            tasks._live_threads.pop(jid, None)

    # Once the thread is dead, the same job is reaped.
    tasks.reap_stale_jobs()
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.error


# ---- uploads: partial file removed when the stream fails mid-write ----

def test_upload_partial_write_is_cleaned_up(tmp_path, monkeypatch):
    import asyncio

    from dragontag.app.ingest import uploads

    class _Env:
        drop_path = tmp_path / "drop"

    monkeypatch.setattr(uploads, "env", lambda: _Env())
    monkeypatch.setattr(uploads, "_validate", lambda u: None)

    class _Upload:
        filename = "song.mp3"
        _reads = 0

        async def read(self, n):
            self._reads += 1
            if self._reads == 1:
                return b"partial data"
            raise ConnectionError("client went away")

    try:
        asyncio.run(uploads.save_uploads([_Upload()]))
    except ConnectionError:
        pass
    leftovers = list((tmp_path / "drop").iterdir())
    assert leftovers == [], f"partial file left behind: {leftovers}"
