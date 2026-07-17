"""Unit tests for the advisory/lyrics extraction added to existing_tags.read."""
from dragontag.app.identify.existing_tags import _norm_advisory, _has_lyrics


def test_norm_advisory_maps_conventions():
    # dragontag writes 1=explicit / 0=clean; iTunes writes 2=clean and
    # historically used 4 for explicit.
    assert _norm_advisory("1") == 1
    assert _norm_advisory("0") == 0
    assert _norm_advisory("2") == 0
    assert _norm_advisory(1) == 1
    assert _norm_advisory("4") == 1
    # Unknown / unparseable ratings -> None.
    assert _norm_advisory(None) is None
    assert _norm_advisory("") is None
    assert _norm_advisory("explicit") is None
    assert _norm_advisory("3") is None


class _FrameTags(dict):
    """Mimic an ID3 tag map: dict access plus a getall() for USLT frames."""

    def __init__(self, uslt=None, **kw):
        super().__init__(**kw)
        self._uslt = uslt or []

    def getall(self, key):
        return self._uslt if key == "USLT" else []


def test_has_lyrics_vorbis_and_mp4():
    assert _has_lyrics({"LYRICS": ["la la"]}) is True
    assert _has_lyrics({"UNSYNCEDLYRICS": ["la la"]}) is True
    assert _has_lyrics({"\xa9lyr": ["la la"]}) is True
    assert _has_lyrics({"TITLE": ["song"]}) is False
    assert _has_lyrics({}) is False
    assert _has_lyrics(None) is False


def test_has_lyrics_id3_uslt():
    assert _has_lyrics(_FrameTags(uslt=["frame"])) is True
    assert _has_lyrics(_FrameTags(uslt=[])) is False


def _make_flac(path):
    """Minimal valid FLAC with a VorbisComment block (mirrors test_partial_genre)."""
    from mutagen.flac import FLAC
    magic = b"fLaC"
    hdr = bytes([0x80]) + (34).to_bytes(3, "big")  # last-block | STREAMINFO, len 34
    sr, ch, bps = 44100, 2, 16
    packed = (sr << 44) | ((ch - 1) << 41) | ((bps - 1) << 36) | 0
    streaminfo = (
        (4096).to_bytes(2, "big") + (4096).to_bytes(2, "big")
        + (0).to_bytes(3, "big") + (0).to_bytes(3, "big")
        + packed.to_bytes(8, "big") + b"\x00" * 16
    )
    path.write_bytes(magic + hdr + streaminfo)
    f = FLAC(str(path))
    f["TITLE"] = ["Song"]
    f["ARTIST"] = ["Band"]
    f.save()


def test_read_flac_with_mp4_aliases_does_not_raise(tmp_path):
    # Regression: read() queries MP4-style keys ("\xa9nam", …) as aliases. On a
    # FLAC the tag container is a mutagen VCommentDict whose .get() raises
    # ValueError on a non-ASCII key instead of returning None, which used to
    # crash every dragged/dropped FLAC. read() must swallow that and return the
    # Vorbis values it can read.
    from dragontag.app.identify import existing_tags

    p = tmp_path / "track.flac"
    _make_flac(p)
    out = existing_tags.read(p)  # must not raise
    assert out["title"] == "Song"
    assert out["artist"] == "Band"
