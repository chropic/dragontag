"""write_genre / read_genre parity across the four tag formats.

WAV exercises the shared ID3 (``TCON``) branch used by MP3 too; a synthesized
minimal FLAC and a fake MP4 cover the other two. A blank/whitespace value must
read back as "no genre" so the Fix-genres action treats a stray ``GENRE=""`` as
missing.
"""
import wave
from pathlib import Path

from dragontag.app.tagging.partial import read_genre, write_genre


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def _make_flac(path: Path) -> None:
    # Minimal valid FLAC: magic + a single (last) STREAMINFO block, no audio
    # frames — enough for mutagen to load and round-trip a VorbisComment.
    magic = b"fLaC"
    hdr = bytes([0x80]) + (34).to_bytes(3, "big")  # last-block flag | type 0, len 34
    sr, ch, bps = 44100, 2, 16
    packed = (sr << 44) | ((ch - 1) << 41) | ((bps - 1) << 36) | 0
    streaminfo = (
        (4096).to_bytes(2, "big") + (4096).to_bytes(2, "big")
        + (0).to_bytes(3, "big") + (0).to_bytes(3, "big")
        + packed.to_bytes(8, "big") + b"\x00" * 16
    )
    path.write_bytes(magic + hdr + streaminfo)


def test_wav_round_trip_and_absence(tmp_path):
    p = tmp_path / "t.wav"
    _make_wav(p)
    assert read_genre(p) == []                # nothing embedded yet
    write_genre(p, ["Rock", "Pop"])
    assert read_genre(p) == ["Rock", "Pop"]


def test_flac_round_trip(tmp_path):
    p = tmp_path / "t.flac"
    _make_flac(p)
    assert read_genre(p) == []
    write_genre(p, ["Hip Hop", "Trap"])
    assert read_genre(p) == ["Hip Hop", "Trap"]


def test_blank_genre_reads_as_absent(tmp_path):
    p = tmp_path / "t.wav"
    _make_wav(p)
    write_genre(p, ["   ", ""])   # all blank -> no-op, nothing written
    assert read_genre(p) == []


def test_mp4_round_trip_via_fake(tmp_path, monkeypatch):
    store: dict = {}

    class _FakeMP4:
        def __init__(self, _path):
            self.tags = store

        def add_tags(self):
            pass

        def save(self):
            pass

    monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
    p = tmp_path / "t.m4a"
    p.write_bytes(b"\x00")
    assert read_genre(p) == []
    write_genre(p, ["Jazz"])
    assert store["\xa9gen"] == ["Jazz"]
    assert read_genre(p) == ["Jazz"]
