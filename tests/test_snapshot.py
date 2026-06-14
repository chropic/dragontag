"""Round-trip test for the revert snapshot: capture original tags, overwrite,
then restore and confirm the original tags come back.

WAV is used because the stdlib ``wave`` module can synthesize a valid file
without an external encoder, and it exercises the ID3 capture/restore path.
"""
import wave
from pathlib import Path

from dragontag.app.config import Separators
from dragontag.app.tagging import snapshot
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.writers.wav import write


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_snapshot_capture_restore_round_trip(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)

    # Lay down the "original" tags, then snapshot them.
    write(p, TrackTags(title="Original", artists=["Orig Artist"], genres=["Jazz"]), Separators())
    snap = snapshot.capture(p)
    assert snap["format"] == "wav"
    assert snap["tags"]["TIT2"] == ["Original"]
    assert snap["tags"]["TPE1"] == ["Orig Artist"]

    # Simulate a destructive dragontag write with different (multi-value) tags.
    write(p, TrackTags(title="New", artists=["New A", "New B"], genres=["Pop"]), Separators())
    assert WAVE(str(p)).tags.getall("TIT2")[0].text == ["New"]

    # Restore the snapshot — original tags return, the new ones are gone.
    snapshot.restore(p, snap)
    restored = WAVE(str(p)).tags
    assert restored.getall("TIT2")[0].text == ["Original"]
    assert restored.getall("TPE1")[0].text == ["Orig Artist"]
    assert restored.getall("TCON")[0].text == ["Jazz"]


def test_capture_never_raises_on_bad_file(tmp_path):
    p = tmp_path / "nope.flac"
    p.write_bytes(b"not really a flac")
    assert snapshot.capture(p) == {"format": "flac", "tags": {}}


def test_snapshot_restores_uslt_lyrics(tmp_path):
    """Pre-existing embedded lyrics (ID3 USLT) survive a tag-write + revert."""
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)

    # Original file carries embedded lyrics.
    write(p, TrackTags(title="Original", lyrics="line one\nline two"), Separators())
    snap = snapshot.capture(p)
    assert snap["tags"]["USLT"] == ["line one\nline two"]

    # Destructive write with no lyrics wipes the USLT frame.
    write(p, TrackTags(title="New"), Separators())
    assert WAVE(str(p)).tags.getall("USLT") == []

    # Restore brings the original lyrics back.
    snapshot.restore(p, snap)
    uslt = WAVE(str(p)).tags.getall("USLT")
    assert uslt and uslt[0].text == "line one\nline two"
