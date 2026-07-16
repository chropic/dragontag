"""Case-twin prevention: destination resolution must be race-proof and
fail-closed.

Two concurrent callers resolving case-variant spellings of one artist must
converge on a single directory (the resolve→mkdir critical section), and an
I/O failure while scanning an existing library dir must raise
``DestinationUnresolved`` instead of silently creating the wanted-case
directory next to a sibling it couldn't see — that fail-open path is exactly
how ``fakemink``/``Fakemink`` twin trees (and phantom files on SMB shares)
were minted.
"""
import os
import threading
import wave
from pathlib import Path

import pytest

from dragontag.app.library import paths as paths_mod
from dragontag.app.library.paths import DestinationUnresolved, build_destination
from dragontag.app.tagging.schema import TrackTags


class _FakeSettings:
    folder_artist_split_separators = ""
    fold_edition_suffixes = True
    filename_template_single = "{track:02d}. {title}.{ext}"
    filename_template_multidisc = "{track:02d}. {title}.{ext}"
    multidisc_folder_template = "Disc {disc}"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(paths_mod, "settings", lambda: _FakeSettings())


def _tags(artist: str, title: str) -> TrackTags:
    return TrackTags(
        title=title, artist_display=artist, album_artist_display=artist,
        album="AlbumA", track=1,
    )


def test_concurrent_case_variants_converge_on_one_dir(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    barrier = threading.Barrier(2)
    results: dict[str, Path] = {}
    errors: list[Exception] = []

    def worker(artist: str) -> None:
        try:
            barrier.wait(timeout=5)
            results[artist] = build_destination(
                _tags(artist, f"T-{artist}"), ".flac",
                library_root=lib, ensure_dirs=True,
            )
        except Exception as e:  # pragma: no cover - failure reporting
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(a,)) for a in ("Fakemink", "fakemink")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    top_dirs = [d.name for d in lib.iterdir() if d.is_dir()]
    assert len(top_dirs) == 1  # exactly one artist dir, no case twin
    for dest in results.values():
        assert dest.parts[len(lib.parts)] == top_dirs[0]


def test_scan_failure_is_fail_closed(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    (lib / "Fakemink" / "AlbumA").mkdir(parents=True)

    real_scandir = os.scandir

    def flaky_scandir(path):
        if Path(path) == lib:
            raise OSError(5, "Input/output error")
        return real_scandir(path)

    monkeypatch.setattr(paths_mod.os, "scandir", flaky_scandir)

    before = sorted(p.name for p in lib.iterdir())
    with pytest.raises(DestinationUnresolved):
        build_destination(
            _tags("fakemink", "T"), ".flac", library_root=lib, ensure_dirs=True
        )
    # Nothing was created: no fakemink twin next to Fakemink.
    assert sorted(p.name for p in lib.iterdir()) == before


def test_missing_parent_still_degrades_benignly(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    dest = build_destination(
        _tags("Newcomer", "T"), ".flac", library_root=lib, ensure_dirs=True
    )
    assert dest.parent.is_dir()
    assert dest.parent.parent.name == "Newcomer"


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_pipeline_routes_unresolved_destination_to_review(tmp_path, monkeypatch):
    """An unresolvable destination sends the job to review — and because tags
    were already rewritten in place, the write must stay auditable via a
    FileChange row (revert still works)."""
    from dragontag.app.db import session
    from dragontag.app.ingest import pipeline
    from dragontag.app.models import FileChange, Job, JobStatus, ReviewReason
    from sqlmodel import select

    p = tmp_path / "song.wav"
    _make_wav(p)

    monkeypatch.setattr(
        pipeline.existing_tags,
        "read",
        lambda _: {"mb_track_id": "rec-1", "mb_album_id": "rel-1", "duration": 1.0},
    )
    monkeypatch.setattr(
        pipeline.mbq,
        "assemble_tags",
        lambda *, release_id, recording_id: TrackTags(
            title="T", artists=["A"], album="Al", track_total=10
        ),
    )
    from dragontag.app.tagging import lyrics_fetcher
    monkeypatch.setattr(lyrics_fetcher, "fetch", lambda **kw: None)

    def boom(tags, ext, **kw):
        raise DestinationUnresolved(tmp_path, "A")

    monkeypatch.setattr(pipeline, "build_destination", boom)

    job = pipeline.enqueue(p, dry_run=False)
    pipeline.process(job.id)

    with session() as s:
        row = s.get(Job, job.id)
        assert row.status == JobStatus.needs_review
        assert row.review_reason == ReviewReason.destination_unresolved
        change = s.exec(
            select(FileChange).where(FileChange.job_id == job.id)
        ).first()
        assert change is not None
        assert change.file_path == str(p)  # file stayed where it was
    assert p.exists()
