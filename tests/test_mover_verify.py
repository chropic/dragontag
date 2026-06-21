"""L1/L2/L3: mover hardening — samefile TOCTOU, size verification, atomic cover."""
import os
from pathlib import Path

import pytest

from dragontag.app.library import mover
from dragontag.app.library.mover import move, write_cover_jpg


def test_samefile_does_not_raise_when_source_vanishes(tmp_path: Path, monkeypatch):
    # destination exists, and os.path.samefile blows up as if the source was
    # deleted between the exists() check and the samefile() call. move() must
    # not propagate that — it falls through to a normal move attempt.
    dest = tmp_path / "d.flac"
    dest.write_bytes(b"existing")
    src = tmp_path / "s.flac"
    src.write_bytes(b"incoming")

    def boom(a, b):
        raise FileNotFoundError("source vanished")

    monkeypatch.setattr(os.path, "samefile", boom)
    # Different file already at dest, overwrite defaults to False → conflict,
    # but crucially no exception escapes.
    result = move(src, dest)
    assert result.conflict is True
    assert src.exists()


def test_cross_volume_size_mismatch_is_detected(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.flac"
    src.write_bytes(b"x" * 100)
    dest = tmp_path / "out" / "a.flac"

    # Simulate shutil.move landing a truncated file (as a flaky network volume
    # might): create a short destination and remove the source.
    def fake_move(s, d):
        Path(d).write_bytes(b"x" * 40)  # truncated!
        Path(s).unlink()

    monkeypatch.setattr(mover.shutil, "move", fake_move)
    with pytest.raises(OSError, match="verification failed"):
        move(src, dest)


def test_overwrite_failure_does_not_destroy_existing_destination(tmp_path: Path, monkeypatch):
    """S4: a failed overwrite move must never lose the file already at
    destination. The old code unlinked destination before attempting
    shutil.move, so a failure partway through left nothing behind."""
    dest = tmp_path / "b.flac"
    dest.write_bytes(b"old-content")
    src = tmp_path / "a.flac"
    src.write_bytes(b"new-content")

    def boom(s, d):
        raise OSError("simulated cross-device failure")

    monkeypatch.setattr(mover.shutil, "move", boom)

    with pytest.raises(OSError, match="simulated cross-device failure"):
        move(src, dest, overwrite=True)

    assert dest.read_bytes() == b"old-content"
    assert src.exists()
    assert not list(tmp_path.glob(".dgmove-*"))


def test_overwrite_replaces_destination_on_success(tmp_path: Path):
    dest = tmp_path / "b.flac"
    dest.write_bytes(b"old-content")
    src = tmp_path / "a.flac"
    src.write_bytes(b"new-content")

    result = move(src, dest, overwrite=True)

    assert result.moved is True
    assert dest.read_bytes() == b"new-content"
    assert not src.exists()
    assert not list(tmp_path.glob(".dgmove-*"))


def test_write_cover_jpg_is_atomic_and_leaves_no_temp(tmp_path: Path):
    folder = tmp_path / "Album"
    out = write_cover_jpg(folder, b"\xff\xd8jpegdata", min_overwrite_pixels=0, new_width=500)
    assert out == folder / "cover.jpg"
    assert out.read_bytes() == b"\xff\xd8jpegdata"
    assert not list(folder.glob(".dgcover-*"))


def test_write_cover_jpg_respects_min_overwrite(tmp_path: Path):
    folder = tmp_path / "Album"
    folder.mkdir()
    (folder / "cover.jpg").write_bytes(b"hi-res")
    # New image narrower than the threshold → keep the curated one.
    out = write_cover_jpg(folder, b"small", min_overwrite_pixels=1000, new_width=300)
    assert out is None
    assert (folder / "cover.jpg").read_bytes() == b"hi-res"
