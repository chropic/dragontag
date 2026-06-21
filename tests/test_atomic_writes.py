"""Atomic tag writes (H1): a crash mid-save must never corrupt the original.

The writers mutate a temp copy and ``os.replace`` it in. We verify both the
happy path (tags round-trip) and the failure path (original is byte-identical
and no ``.dgtag-*`` temp is left behind).
"""
import wave
from pathlib import Path

import pytest

from dragontag.app.config import Separators
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.writers import _atomic
from dragontag.app.tagging.writers.wav import write


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_write_round_trips_tags(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)
    write(p, TrackTags(title="Hello", artists=["A"]), Separators())

    assert WAVE(str(p)).tags.getall("TIT2")[0].text == ["Hello"]
    # No temp files left around.
    assert not list(tmp_path.glob(".dgtag-*"))


def test_save_failure_leaves_original_intact(tmp_path, monkeypatch):
    p = tmp_path / "t.wav"
    _make_wav(p)
    original = p.read_bytes()

    # Force a failure *inside* the atomic block (after the temp copy exists,
    # before os.replace) by making the real os.replace blow up.
    real_replace = _atomic.os.replace

    def boom(src, dst):
        raise RuntimeError("simulated crash during swap")

    monkeypatch.setattr(_atomic.os, "replace", boom)
    with pytest.raises(RuntimeError):
        write(p, TrackTags(title="Should not land"), Separators())
    monkeypatch.setattr(_atomic.os, "replace", real_replace)

    # Original file is untouched and no temp survived.
    assert p.read_bytes() == original
    assert not list(tmp_path.glob(".dgtag-*"))


def test_atomic_inplace_cleans_temp_on_body_error(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"keepme")
    with pytest.raises(ValueError):
        with _atomic.atomic_inplace(p) as tmp:
            tmp.write_bytes(b"partial")
            raise ValueError("boom")
    assert p.read_bytes() == b"keepme"
    assert not list(tmp_path.glob(".dgtag-*"))


def test_atomic_inplace_fsyncs_before_and_after_swap(tmp_path, monkeypatch):
    p = tmp_path / "f.bin"
    p.write_bytes(b"keepme")
    calls = []
    real_fsync = _atomic.os.fsync
    monkeypatch.setattr(_atomic.os, "fsync", lambda fd: (calls.append(fd), real_fsync(fd))[1])

    with _atomic.atomic_inplace(p) as tmp:
        tmp.write_bytes(b"newdata")

    assert p.read_bytes() == b"newdata"
    assert len(calls) == 2  # one for the temp file's data, one for the directory


def test_cleanup_orphaned_temp_files_sweeps_stale_temps(tmp_path):
    (tmp_path / "sub").mkdir()
    orphan = tmp_path / "sub" / ".dgtag-abc123.flac"
    orphan.write_bytes(b"leftover")
    keep = tmp_path / "sub" / "real.flac"
    keep.write_bytes(b"real")

    removed = _atomic.cleanup_orphaned_temp_files(tmp_path)

    assert removed == 1
    assert not orphan.exists()
    assert keep.exists()
