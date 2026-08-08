"""The ingest duplicate gate runs before cover, lyrics, tags, or moves."""
from __future__ import annotations

import uuid
import wave
from pathlib import Path

from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.ingest import pipeline
from dragontag.app.library.duplicates import find_duplicate_tracks, is_duplicate_track
from dragontag.app.models import LibraryFolder, Job, JobStatus, ReviewReason, Track
from dragontag.app.tagging.schema import TrackTags


def _wav(path: Path, *, frames: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * frames)


def _selected_folder() -> LibraryFolder:
    with session() as s:
        folder = s.exec(
            select(LibraryFolder)
            .where(LibraryFolder.enabled == True)  # noqa: E712
            .order_by(LibraryFolder.priority, LibraryFolder.id)
        ).first()
        assert folder is not None
        s.expunge(folder)
        return folder


def _track(folder_id: int, *, path: Path, **values) -> Track:
    track = Track(path=str(path), library_folder_id=folder_id, **values)
    with session() as s:
        s.add(track)
        s.commit()
        s.refresh(track)
        s.expunge(track)
    return track


def test_duplicate_semantics_mbid_metadata_and_duration():
    track = Track(
        path="library/song.flac",
        mb_track_id="recording-1",
        artist=" Aminé ",
        title="Caroline",
        duration=179.0,
    )
    assert is_duplicate_track(
        track, mb_track_id="recording-1", artist=None, title=None, duration=None
    )
    assert is_duplicate_track(
        track, mb_track_id=None, artist="AMINÉ", title="  Caroline ", duration=182.0
    )
    assert not is_duplicate_track(
        track, mb_track_id=None, artist="Aminé", title="Caroline", duration=182.01
    )


def test_same_physical_path_is_excluded(tmp_path):
    source = tmp_path / "Same.wav"
    _wav(source)
    track = Track(
        path=str(source), artist="Artist", title="Song", duration=1.0
    )
    assert find_duplicate_tracks(
        [track], mb_track_id=None, artist="Artist", title="Song", duration=1.0,
        exclude_paths=[source],
    ) == []


def test_duplicate_lookup_is_scoped_to_destination_library(tmp_path):
    selected = _selected_folder()
    other = LibraryFolder(
        path=str(tmp_path / "other-library"),
        label=f"other-{uuid.uuid4().hex}",
        enabled=True,
        priority=999,
    )
    with session() as s:
        s.add(other)
        s.commit()
        s.refresh(other)
        other_id = other.id
    _track(
        other_id,
        path=tmp_path / "other-library" / f"{uuid.uuid4().hex}.flac",
        mb_track_id=f"rec-{uuid.uuid4().hex}",
        artist="Artist",
        title="Scoped song",
        duration=1.0,
    )
    source = tmp_path / "drop" / "scoped.wav"
    _wav(source)
    tags = TrackTags(title="Scoped song", artist_display="Artist")

    assert pipeline.find_library_duplicate_paths(
        source, tags, library_root=Path(selected.path)
    ) == []


def test_commit_gate_leaves_source_untouched_and_records_paths(tmp_path, monkeypatch):
    selected = _selected_folder()
    token = uuid.uuid4().hex
    duplicate_path = Path(selected.path) / "Artist" / "Album" / f"{token}.flac"
    _track(
        selected.id,
        path=duplicate_path,
        mb_track_id=f"rec-{token}",
        artist="Artist",
        title="Duplicate song",
        duration=1.0,
    )
    source = tmp_path / "drop" / f"{token}.wav"
    _wav(source)
    before = source.read_bytes()
    tags = TrackTags(
        title="Duplicate song",
        artist_display="Artist",
        artists=["Artist"],
        album="Album",
        album_artist_display="Artist",
        album_artists=["Artist"],
        mb_track_id=f"rec-{token}",
        mb_album_id=f"rel-{token}",
    )
    with session() as s:
        job = Job(
            source_path=str(source),
            original_name=source.name,
            status=JobStatus.tagging,
            chosen_tags_json=pipeline._tags_to_dict(tags),
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id

    monkeypatch.setattr(
        pipeline, "fetch_for_release",
        lambda _rid: (_ for _ in ()).throw(AssertionError("cover fetch ran")),
    )
    monkeypatch.setattr(
        pipeline, "write_tags",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tag write ran")),
    )

    with session() as s:
        job = s.get(Job, job_id)
        pipeline._commit_tag_path(s, job, source, tags, score=1.0)

    assert source.read_bytes() == before
    with session() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.needs_review
        assert job.review_reason == ReviewReason.duplicate_detected
        assert job.candidates_json["duplicates"] == [str(duplicate_path)]
        candidate = job.candidates_json["items"][0]
        assert candidate["recording_id"] == f"rec-{token}"
        assert candidate["artist"] == "Artist"


def test_duplicate_reason_is_deliberate_second_apply_override(monkeypatch):
    from dragontag.app import main as main_mod

    job = Job(
        source_path="missing.wav",
        original_name="missing.wav",
        status=JobStatus.needs_review,
        review_reason=ReviewReason.duplicate_detected,
    )
    monkeypatch.setattr(
        pipeline, "find_library_duplicate_paths",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gate re-ran")),
    )
    assert main_mod._preflight_duplicate(
        None, job, TrackTags(title="Song", artist_display="Artist")
    ) == []
