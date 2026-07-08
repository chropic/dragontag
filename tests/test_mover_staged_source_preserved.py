"""A failed staged replace must never destroy the incoming file: once the
source has been moved into the temp slot, the temp IS the source, and cleanup
must move it back (not unlink it).
"""
import pytest

from dragontag.app.library import mover


def test_staged_replace_failure_restores_source(tmp_path, monkeypatch):
    src = tmp_path / "incoming.flac"
    src.write_bytes(b"NEW-DATA")
    dest = tmp_path / "lib" / "song.flac"
    dest.parent.mkdir()
    dest.write_bytes(b"OLD-DATA")

    def boom(a, b):
        raise OSError("simulated failure at the final swap")

    monkeypatch.setattr(mover.os, "replace", boom)

    with pytest.raises(OSError):
        mover.move(src, dest, overwrite=True)

    # Incoming file restored, destination untouched, no orphan temp left.
    assert src.read_bytes() == b"NEW-DATA"
    assert dest.read_bytes() == b"OLD-DATA"
    assert not list(dest.parent.glob(".dgmove-*"))


def test_staged_replace_success_path_still_works(tmp_path):
    src = tmp_path / "incoming.flac"
    src.write_bytes(b"NEW-DATA")
    dest = tmp_path / "lib" / "song.flac"
    dest.parent.mkdir()
    dest.write_bytes(b"OLD-DATA")

    res = mover.move(src, dest, overwrite=True)

    assert res.moved
    assert dest.read_bytes() == b"NEW-DATA"
    assert not src.exists()
