"""Multi-value fields must be written as native multiple values, not a single
joined string — otherwise Navidrome/Picard read e.g. ARTIST as one artist.

A WAV file is used because the stdlib ``wave`` module can synthesize a valid
one without an external encoder, and mutagen writes the same ID3 frames into
WAV as it does for MP3 (both go through ``populate_id3``).
"""
import wave
from pathlib import Path

from dragontag.app.config import Separators
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.writers.wav import write


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_wav_id3_round_trip_writes_multiple_values(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)
    tags = TrackTags(
        title="x",
        artists=["A", "B", "C"],
        album_artist_display="A & B",
        album_artists=["A", "B"],
        genres=["Rock", "Pop"],
    )
    write(p, tags, Separators())

    audio = WAVE(str(p))
    assert audio.tags.getall("TPE1")[0].text == ["A", "B", "C"]   # artist
    assert audio.tags.getall("TPE2")[0].text == ["A", "B"]        # album artist
    assert audio.tags.getall("TCON")[0].text == ["Rock", "Pop"]   # genre
    assert audio.tags.getall("TXXX:ARTISTS")[0].text == ["A", "B", "C"]
