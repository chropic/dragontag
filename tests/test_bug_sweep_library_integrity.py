"""Library DB/disk integrity fixes from the 2026-07-10 sweep.

* ``_upsert_track`` re-points the Track row indexed at the pre-move path when
  a re-tagged library file lands at a new canonical destination — instead of
  inserting a duplicate and leaving a phantom row at the old path.
* ``move_back`` brings the ``.lrc`` sidecar home with the audio file.
* ``scanner._prune_missing`` detaches ``Job.track_id`` before deleting a
  Track row, matching the manual delete route.
"""
from pathlib import Path

from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.ingest.pipeline import _upsert_track
from dragontag.app.models import FileChange, Job, JobStatus, LibraryFolder, Track
from dragontag.app.tagging.schema import TrackTags


# ---- _upsert_track: re-point instead of phantom duplicate ----

def test_upsert_track_repoints_row_from_original_path(tmp_path):
    lib = tmp_path / "lib"
    old = lib / "Old Artist" / "Old Album" / "01. song.wav"
    new = lib / "New Artist" / "New Album" / "01. song.wav"
    new.parent.mkdir(parents=True)
    new.write_bytes(b"\x00")  # unreadable header is fine — read() degrades

    with session() as s:
        row = Track(path=str(old), title="Old", protected=True)
        s.add(row)
        s.commit()
        s.refresh(row)
        old_id = row.id

    tags = TrackTags(title="New", artist_display="New Artist", album="New Album")
    with session() as s:
        track = _upsert_track(s, new, tags, lib, original_path=str(old))
        assert track.id == old_id            # same row, re-pointed
        assert track.path == str(new)
        assert track.protected is True       # flag survives the move
        rows = s.exec(select(Track).where(
            Track.path.in_([str(old), str(new)])
        )).all()
        assert len(rows) == 1                # no phantom left at the old path
        s.delete(rows[0])
        s.commit()


# ---- move_back: lyric sidecar follows the audio ----

def test_move_back_moves_lyric_sidecar(tmp_path):
    from dragontag.app.library.revert import move_back

    cur = tmp_path / "lib" / "Artist" / "Album" / "01. song.mp3"
    cur.parent.mkdir(parents=True)
    cur.write_bytes(b"\x00audio")
    cur.with_suffix(".lrc").write_text("[00:01.00] la", encoding="utf-8")
    orig = tmp_path / "drop" / "song.mp3"
    orig.parent.mkdir(parents=True)

    with session() as s:
        change = FileChange(file_path=str(cur), original_path=str(orig))
        s.add(change)
        s.commit()
        s.refresh(change)
        cid = change.id

    ok, msg = move_back(cid)
    assert ok, msg
    assert orig.exists()
    assert orig.with_suffix(".lrc").exists()          # sidecar came home
    assert not cur.with_suffix(".lrc").exists()       # not orphaned in the library


# ---- _prune_missing: jobs are detached, not left dangling ----

def test_scan_prune_detaches_job_track_id(tmp_path):
    from dragontag.app.library.scanner import scan_folder

    with session() as s:
        folder = LibraryFolder(path=str(tmp_path), label="prune-test")
        s.add(folder)
        s.commit()
        s.refresh(folder)
        fid = folder.id
        track = Track(path=str(tmp_path / "gone.mp3"), library_folder_id=fid)
        s.add(track)
        s.commit()
        s.refresh(track)
        job = Job(
            source_path="x", original_name="x",
            status=JobStatus.done, track_id=track.id,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        jid = job.id

    scan_folder(tmp_path, fid)  # file never existed → row pruned

    with session() as s:
        assert s.get(Job, jid).track_id is None
        assert s.exec(select(Track).where(Track.library_folder_id == fid)).first() is None
        s.delete(s.get(LibraryFolder, fid))
        s.commit()
