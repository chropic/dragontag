"""write_basic_tags must split an unsplit "X feat. Y" artist/album_artist
string into proper multi-value tag frames, just like the full MB-assembled
writers do for credits that arrive pre-split.
"""
import wave
from pathlib import Path

from dragontag.app.tagging.partial import write_album_link_tags, write_basic_tags


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


def test_write_album_link_tags_writes_album_fields_only(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "song.wav"
    _make_wav(p)
    write_album_link_tags(
        p,
        album="Shared Album",
        album_artist="Diplo, SIDEPIECE",
        disc_total=2,
        track_total=10,
        mb_album_id="album-mbid",
        mb_release_group_id="rg-mbid",
    )
    audio = WAVE(str(p))
    assert audio.tags.getall("TALB")[0].text == ["Shared Album"]
    assert audio.tags.getall("TPE2")[0].text == ["Diplo", "SIDEPIECE"]
    assert audio.tags.getall("TRCK")[0].text[0].endswith("/10")
    assert audio.tags.getall("TPOS")[0].text[0].endswith("/2")
    assert audio.tags.getall("TXXX:MusicBrainz Album Id")[0].text == ["album-mbid"]
    assert audio.tags.getall("TXXX:MusicBrainz Release Group Id")[0].text == ["rg-mbid"]
    # Title/artist were never passed and must be untouched (no TIT2/TPE1 frames).
    assert not audio.tags.getall("TIT2")
    assert not audio.tags.getall("TPE1")
