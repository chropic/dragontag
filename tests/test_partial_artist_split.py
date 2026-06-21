"""write_basic_tags must split an unsplit "X feat. Y" artist/album_artist
string into proper multi-value tag frames, just like the full MB-assembled
writers do for credits that arrive pre-split.
"""
import wave
from pathlib import Path

from dragontag.app.tagging.partial import write_basic_tags


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_write_basic_tags_splits_feat_artist(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "song.wav"
    _make_wav(p)
    write_basic_tags(
        p, title=None, artist="2hollis feat. nate sib", album=None,
        album_artist="Diplo, SIDEPIECE", track=None, track_total=None,
        disc=None, disc_total=None,
    )
    audio = WAVE(str(p))
    assert audio.tags.getall("TPE1")[0].text == ["2hollis", "nate sib"]
    assert audio.tags.getall("TPE2")[0].text == ["Diplo", "SIDEPIECE"]


def test_write_basic_tags_passthrough_single_artist(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "song.wav"
    _make_wav(p)
    write_basic_tags(
        p, title=None, artist="Daft Punk", album=None,
        album_artist=None, track=None, track_total=None,
        disc=None, disc_total=None,
    )
    audio = WAVE(str(p))
    assert audio.tags.getall("TPE1")[0].text == ["Daft Punk"]
