"""cleanup_library pass 0: merge top-level artist directories that fold equal
(case/punctuation twins like ``fakemink``/``Fakemink`` — the twin trees that
produced phantom files on a case-insensitive view of the library share).
Strict fold equality only; conservative cover handling (distinct images stay
in the album folder, only byte-identical duplicates are quarantined)."""
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library import actions
from dragontag.app.models import LibraryFolder, Track


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="t")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    yield fid, tmp_path
    with session() as s:
        for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all():
            s.delete(t)
        row = s.get(LibraryFolder, fid)
        if row:
            s.delete(row)
        s.commit()


def _flac(path: Path, data: bytes = b"audio"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _track(fid, path: Path, **kw):
    with session() as s:
        t = Track(library_folder_id=fid, path=str(path), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _all_files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _twin_library(fid, root: Path):
    # Fakemink holds 2 audio files (wins), fakemink 1 + a sidecar + covers.
    _flac(root / "Fakemink" / "Terrified" / "01. Terrified.flac", b"a1")
    _flac(root / "Fakemink" / "Crush" / "01. Crush.flac", b"a2")
    _flac(root / "fakemink" / "MAKKA" / "01. MAKKA.flac", b"a3")
    (root / "fakemink" / "MAKKA" / "01. MAKKA.lrc").write_bytes(b"lyrics")
    (root / "fakemink" / "MAKKA" / "cover.jpg").write_bytes(b"distinct-art")
    _track(fid, root / "Fakemink" / "Terrified" / "01. Terrified.flac",
           album_artist="Fakemink")
    _track(fid, root / "Fakemink" / "Crush" / "01. Crush.flac",
           album_artist="Fakemink")
    tid = _track(fid, root / "fakemink" / "MAKKA" / "01. MAKKA.flac",
                 album_artist="fakemink")
    return tid


def test_report_mode_detects_but_changes_nothing(folder):
    fid, root = folder
    _twin_library(fid, root)
    before = _all_files(root)

    out = actions.cleanup_library(fid, apply=False)

    assert out["artist_twin_groups"] == 1
    assert _all_files(root) == before
    assert not (root / ".dragontag-trash").exists()


def test_apply_merges_case_twin_artists(folder):
    fid, root = folder
    tid = _twin_library(fid, root)

    out = actions.cleanup_library(fid, apply=True)

    assert out["artist_twin_groups"] == 1
    # Loser tree merged into the audio-majority spelling and pruned.
    assert not (root / "fakemink").exists()
    dest = root / "Fakemink" / "MAKKA" / "01. MAKKA.flac"
    assert dest.exists()
    # Sidecar followed; the distinct cover stayed with its album (not trashed).
    assert (root / "Fakemink" / "MAKKA" / "01. MAKKA.lrc").exists()
    assert (root / "Fakemink" / "MAKKA" / "cover.jpg").exists()
    trash = root / ".dragontag-trash"
    assert not any(
        p.suffix == ".jpg" for p in trash.rglob("*")
    ) if trash.exists() else True
    # Track row repointed.
    with session() as s:
        assert s.get(Track, tid).path == str(dest)


def test_apply_quarantines_only_byte_identical_covers(folder):
    fid, root = folder
    _flac(root / "Fakemink" / "Crush" / "01. Crush.flac", b"a1")
    _flac(root / "fakemink" / "Crush" / "02. Crush2.flac", b"a2")
    (root / "Fakemink" / "Crush" / "cover.jpg").write_bytes(b"same-bytes")
    (root / "fakemink" / "Crush" / "cover.jpg").write_bytes(b"same-bytes")
    _track(fid, root / "Fakemink" / "Crush" / "01. Crush.flac", album_artist="Fakemink")
    _track(fid, root / "Fakemink" / "Crush" / "01b.flac", album_artist="Fakemink")
    _track(fid, root / "fakemink" / "Crush" / "02. Crush2.flac", album_artist="fakemink")
    _flac(root / "Fakemink" / "Crush" / "01b.flac", b"a3")  # Fakemink wins on count

    out = actions.cleanup_library(fid, apply=True)

    assert not (root / "fakemink").exists()
    # Exactly one cover.jpg remains in the merged album; the identical twin
    # went to the trash, nothing else did.
    covers = list((root / "Fakemink" / "Crush").glob("cover*"))
    assert [c.name for c in covers] == ["cover.jpg"]
    assert out["covers_deduped"] >= 1


def test_protected_track_not_moved(folder):
    fid, root = folder
    _flac(root / "Fakemink" / "A" / "01.flac", b"x")
    _flac(root / "Fakemink" / "A" / "02.flac", b"y")
    _flac(root / "fakemink" / "B" / "01.flac", b"z")
    _track(fid, root / "Fakemink" / "A" / "01.flac", album_artist="Fakemink")
    _track(fid, root / "Fakemink" / "A" / "02.flac", album_artist="Fakemink")
    _track(fid, root / "fakemink" / "B" / "01.flac", album_artist="fakemink",
           protected=True)

    out = actions.cleanup_library(fid, apply=True)

    assert out["skipped_protected"] == 1
    assert (root / "fakemink" / "B" / "01.flac").exists()  # left in place


def test_digit_differing_artists_never_merged(folder):
    fid, root = folder
    _flac(root / "jonatan leandoer96" / "A" / "01.flac")
    _flac(root / "Jonatan Leandoer127" / "B" / "01.flac")

    out = actions.cleanup_library(fid, apply=True)

    assert out["artist_twin_groups"] == 0
    assert (root / "jonatan leandoer96").exists()
    assert (root / "Jonatan Leandoer127").exists()
