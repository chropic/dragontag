from pathlib import Path

from dragontag.app.library.organizer import _prune_empty_dirs


def test_prune_empty_dirs_removes_empty_only(tmp_path: Path):
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    a = lib_root / "ArtistA" / "Album1"
    b = lib_root / "ArtistB" / "Album2"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    # b still has a file → must not be removed.
    (b / "track.flac").write_bytes(b"x")

    removed = _prune_empty_dirs({a, b}, lib_root)

    # ArtistA + Album1 should both go (both empty after the "move").
    assert not a.exists()
    assert not (lib_root / "ArtistA").exists()
    # b is intact because it contains a file.
    assert b.exists()
    assert (b / "track.flac").exists()
    assert removed >= 2


def test_prune_empty_dirs_never_removes_library_root(tmp_path: Path):
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    _prune_empty_dirs({lib_root}, lib_root)
    assert lib_root.exists()


def test_prune_empty_dirs_skips_outside_library(tmp_path: Path):
    lib_root = tmp_path / "lib"
    lib_root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _prune_empty_dirs({outside}, lib_root)
    # Outside dirs are not touched even when empty.
    assert outside.exists()
