"""write_basic_tags: the MP4 branch must preserve the untouched half of the
(number, total) tuples, and clear_blanks=True must delete blanked fields from
the file (the track-edit modal posts every field, so a blank is a deliberate
clear — leaving the file tag behind meant the next scan resurrected it).
"""
import wave
from pathlib import Path

from dragontag.app.tagging.partial import write_basic_tags

_NONE = dict(
    title=None, artist=None, album=None, album_artist=None,
    track=None, track_total=None, disc=None, disc_total=None,
)


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


class _FakeMP4:
    """Stands in for mutagen.mp4.MP4 — no encoder can synthesize a real
    M4A in the test env, and only the tag-dict logic is under test."""

    store: dict = {}

    def __init__(self, _path):
        self.tags = _FakeMP4.store

    def save(self):
        pass


def _patch_mp4(monkeypatch, initial: dict):
    _FakeMP4.store = dict(initial)
    monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
    return _FakeMP4


def test_mp4_track_edit_preserves_existing_total(tmp_path, monkeypatch):
    p = tmp_path / "song.m4a"
    p.write_bytes(b"\x00")
    fake = _patch_mp4(monkeypatch, {"trkn": [(5, 12)], "disk": [(1, 2)]})

    write_basic_tags(p, **{**_NONE, "track": 6})

    assert fake.store["trkn"] == [(6, 12)]  # total 12 must survive
    assert fake.store["disk"] == [(1, 2)]   # untouched pair left alone


def test_mp4_total_edit_preserves_existing_number(tmp_path, monkeypatch):
    p = tmp_path / "song.m4a"
    p.write_bytes(b"\x00")
    fake = _patch_mp4(monkeypatch, {"trkn": [(5, 12)]})

    write_basic_tags(p, **{**_NONE, "track_total": 20})

    assert fake.store["trkn"] == [(5, 20)]


def test_mp4_clear_blanks_removes_pairs_and_text_atoms(tmp_path, monkeypatch):
    p = tmp_path / "song.m4a"
    p.write_bytes(b"\x00")
    fake = _patch_mp4(
        monkeypatch,
        {"trkn": [(5, 12)], "\xa9alb": ["Old Album"], "\xa9nam": ["Old Title"]},
    )

    write_basic_tags(p, **{**_NONE, "title": "Kept"}, clear_blanks=True)

    assert fake.store["\xa9nam"] == ["Kept"]
    assert "\xa9alb" not in fake.store
    assert "trkn" not in fake.store


def test_id3_clear_blanks_removes_frames(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "song.wav"
    _make_wav(p)
    write_basic_tags(p, **{**_NONE, "title": "T", "album": "Al", "track": 3})
    audio = WAVE(str(p))
    assert audio.tags.getall("TALB")

    write_basic_tags(p, **{**_NONE, "title": "T2"}, clear_blanks=True)
    audio = WAVE(str(p))
    assert audio.tags.getall("TIT2")[0].text == ["T2"]
    assert not audio.tags.getall("TALB")
    assert not audio.tags.getall("TRCK")


def test_id3_default_still_leaves_blanks_as_is(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "song.wav"
    _make_wav(p)
    write_basic_tags(p, **{**_NONE, "title": "T", "album": "Al"})
    # Default (album-consistency style) call: None fields untouched.
    write_basic_tags(p, **{**_NONE, "title": "T2"})
    audio = WAVE(str(p))
    assert audio.tags.getall("TALB")[0].text == ["Al"]
