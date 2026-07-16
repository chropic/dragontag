"""cleanup_library: merge edition-suffix twins, dedupe covers, quarantine dead
folders/leftovers. Report mode never touches disk; apply mode moves files (never
deletes), repoints Track rows, and quarantines non-audio leftovers."""
import io
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
        s.add(Track(library_folder_id=fid, path=str(path), **kw))
        s.commit()


def _png(path: Path, px: int):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (px, px), (10, 20, 30)).save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _all_files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_report_mode_changes_nothing(folder):
    fid, root = folder
    _flac(root / "Future" / "Afraid" / "01. Afraid.flac")
    _flac(root / "Future" / "Afraid - Single" / "01. Afraid.flac")
    (root / "Future" / "Afraid - Single" / "cover.jpg").write_bytes(b"img")
    _flac(root / "Dead" / "cover.jpg")  # non-audio leftover, no audio below
    before = _all_files(root)

    out = actions.cleanup_library(fid, apply=False)

    assert out["mode"] == "report"
    assert out["twin_groups"] == 1
    assert out["dead_folders"] == 1
    assert _all_files(root) == before          # nothing moved
    assert not (root / ".dragontag-trash").exists()


def test_report_covers_deduped_only_byte_identical(folder):
    fid, root = folder
    # base album + a twin whose folder carries three cover images: one
    # byte-identical duplicate of cover.jpg and one visually distinct image.
    _flac(root / "A" / "Album" / "01. Song.flac")
    _flac(root / "A" / "Album - Single" / "01. Song.flac")
    (root / "A" / "Album - Single" / "cover.jpg").write_bytes(b"i1")
    (root / "A" / "Album - Single" / "front.jpg").write_bytes(b"i1")   # identical
    (root / "A" / "Album - Single" / "folder.jpg").write_bytes(b"i2")  # distinct

    out = actions.cleanup_library(fid, apply=False)

    # Only the byte-identical duplicate counts; the distinct image is kept
    # (never quarantined) and not double-counted by the twin-merge pass.
    assert out["covers_deduped"] == 1
    assert not (root / ".dragontag-trash").exists()  # report changes nothing


def test_apply_merges_twins_and_quarantines(folder):
    fid, root = folder
    _flac(root / "Future" / "Afraid" / "01. Afraid.flac", b"keep")
    _flac(root / "Future" / "Afraid - Single" / "02. Other.flac", b"other")
    (root / "Future" / "Afraid - Single" / "notes.nfo").write_bytes(b"nfo")
    _track(fid, root / "Future" / "Afraid" / "01. Afraid.flac")
    _track(fid, root / "Future" / "Afraid - Single" / "02. Other.flac")

    out = actions.cleanup_library(fid, apply=True)

    assert out["mode"] == "apply"
    # audio consolidated under the base "Afraid"
    assert (root / "Future" / "Afraid" / "02. Other.flac").exists()
    assert not (root / "Future" / "Afraid - Single").exists()   # emptied + pruned
    # Track row repointed
    with session() as s:
        paths = {t.path for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all()}
    assert str(root / "Future" / "Afraid" / "02. Other.flac") in paths
    # leftover quarantined preserving relative path; nothing deleted
    trash = list((root / ".dragontag-trash").rglob("notes.nfo"))
    assert trash and trash[0].parts[-3:] == ("Future", "Afraid - Single", "notes.nfo")
    # audio is NEVER under the quarantine dir
    assert not any(p.suffix == ".flac" for p in (root / ".dragontag-trash").rglob("*"))


def test_apply_filename_conflict_uses_unique_suffix(folder):
    fid, root = folder
    _flac(root / "A" / "Album" / "01. Song.flac", b"aaa")
    _flac(root / "A" / "Album - Single" / "01. Song.flac", b"bbb")
    _track(fid, root / "A" / "Album" / "01. Song.flac")
    _track(fid, root / "A" / "Album - Single" / "01. Song.flac")

    out = actions.cleanup_library(fid, apply=True)

    assert out["conflicts"] >= 1
    assert (root / "A" / "Album" / "01. Song.flac").exists()
    assert (root / "A" / "Album" / "01. Song-1.flac").exists()   # conflict-renamed loser
    with session() as s:
        paths = {t.path for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all()}
    assert str(root / "A" / "Album" / "01. Song-1.flac") in paths


def test_protected_track_not_moved(folder):
    fid, root = folder
    _flac(root / "A" / "Album" / "01. Song.flac")
    _flac(root / "A" / "Album - Single" / "02. Prot.flac")
    _track(fid, root / "A" / "Album" / "01. Song.flac")
    _track(fid, root / "A" / "Album - Single" / "02. Prot.flac", protected=True)

    out = actions.cleanup_library(fid, apply=True)

    assert out["skipped_protected"] == 1
    assert (root / "A" / "Album - Single" / "02. Prot.flac").exists()  # left in place


def test_disc_subfolder_preserved_under_target(folder):
    fid, root = folder
    _flac(root / "A" / "Album" / "01. Song.flac")
    _flac(root / "A" / "Album - Single" / "Disc 2" / "03. B.flac")
    _track(fid, root / "A" / "Album - Single" / "Disc 2" / "03. B.flac")

    actions.cleanup_library(fid, apply=True)

    assert (root / "A" / "Album" / "Disc 2" / "03. B.flac").exists()


def test_cover_election_keeps_widest(folder):
    fid, root = folder
    _flac(root / "A" / "Album" / "01. Song.flac")
    _png(root / "A" / "Album" / "cover.jpg", 200)          # narrower canonical
    _flac(root / "A" / "Album - Single" / "01. Song.flac")
    _png(root / "A" / "Album - Single" / "cover.jpg", 800)  # wider
    _track(fid, root / "A" / "Album" / "01. Song.flac")
    _track(fid, root / "A" / "Album - Single" / "01. Song.flac")

    actions.cleanup_library(fid, apply=True)

    from dragontag.app.library.mover import _image_width
    kept = root / "A" / "Album" / "cover.jpg"
    assert kept.exists()
    assert _image_width(kept) == 800                        # the wider survived
    assert not any(p.suffix == ".flac" for p in (root / ".dragontag-trash").rglob("*"))
