from pathlib import Path

from dragontag.app.library.mover import move


def test_move_to_new_path(tmp_path: Path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"audio")
    dest = tmp_path / "Artist" / "Album" / "01. a.flac"

    result = move(src, dest)

    assert result.moved is True
    assert result.conflict is False
    assert dest.exists()
    assert not src.exists()


def test_move_conflict_when_different_file_exists(tmp_path: Path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"new")
    dest = tmp_path / "b.flac"
    dest.write_bytes(b"old")

    result = move(src, dest)

    # A genuine collision with a *different* file is surfaced, not overwritten.
    assert result.moved is False
    assert result.conflict is True
    assert src.exists()
    assert dest.read_bytes() == b"old"


def test_move_in_place_is_noop_not_conflict(tmp_path: Path):
    # Re-tagging a file already at its canonical path: source == destination.
    # This is what the Re-tag / Nuclear batches do, and it must not be treated
    # as a destination conflict (which would flood the review queue).
    p = tmp_path / "Artist" / "Album" / "01. song.flac"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"audio")

    result = move(p, p)

    assert result.moved is True
    assert result.conflict is False
    assert p.exists()
    assert p.read_bytes() == b"audio"


def test_move_overwrite_same_file_does_not_destroy_it(tmp_path: Path):
    # A "Replace" on a self-conflict (overwrite=True, same file) must not
    # unlink-then-move the file out of existence.
    p = tmp_path / "song.flac"
    p.write_bytes(b"audio")

    result = move(p, p, overwrite=True)

    assert result.moved is True
    assert p.exists()
    assert p.read_bytes() == b"audio"
