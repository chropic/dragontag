"""dragontag must be able to read back the MusicBrainz ids its own writers
embed. ID3 keys MB ids under ``TXXX:<NAME>`` / ``UFID:http://musicbrainz.org``
and MP4 under ``----:com.apple.iTunes:<NAME>`` — the reader used to query only
the bare Vorbis names, so the MBID short-circuit never fired for MP3/WAV/MP4
files dragontag itself had tagged.
"""
import wave
from pathlib import Path

from dragontag.app.config import Separators
from dragontag.app.identify.existing_tags import _coerce, read
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.writers.wav import write as write_wav


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def test_id3_mb_ids_round_trip_through_own_writer(tmp_path):
    p = tmp_path / "t.wav"
    _make_wav(p)
    write_wav(
        p,
        TrackTags(
            title="T",
            mb_track_id="rec-mbid",
            mb_album_id="rel-mbid",
            mb_release_group_id="rg-mbid",
            mb_album_artist_ids=["aa-mbid"],
        ),
        Separators(),
    )

    got = read(p)
    assert got["mb_track_id"] == "rec-mbid"
    assert got["mb_album_id"] == "rel-mbid"
    assert got["mb_release_group_id"] == "rg-mbid"
    assert got["mb_album_artist_id"] == "aa-mbid"


def test_picard_style_txxx_descs_are_read(tmp_path):
    import mutagen.id3 as _id3
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)
    f = WAVE(str(p))
    f.add_tags()
    f.tags.add(_id3.TXXX(encoding=3, desc="MusicBrainz Album Id", text=["rel-mbid"]))
    f.tags.add(_id3.UFID(owner="http://musicbrainz.org", data=b"rec-mbid"))
    f.save()

    got = read(p)
    assert got["mb_album_id"] == "rel-mbid"
    assert got["mb_track_id"] == "rec-mbid"


class _UFID:
    def __init__(self, data):
        self.data = data


def test_coerce_handles_ufid_frames_and_freeform_bytes():
    assert _coerce(_UFID(b"rec-mbid")) == "rec-mbid"
    # MP4 freeform atoms arrive as a list of bytes (MP4FreeForm).
    assert _coerce([b"rel-mbid"]) == "rel-mbid"
