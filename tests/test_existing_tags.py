"""Unit tests for the advisory/lyrics extraction added to existing_tags.read."""
from dragontag.app.identify.existing_tags import _norm_advisory, _has_lyrics


def test_norm_advisory_maps_conventions():
    # dragontag writes 1=explicit / 0=clean; iTunes writes 2=clean.
    assert _norm_advisory("1") == 1
    assert _norm_advisory("0") == 0
    assert _norm_advisory("2") == 0
    assert _norm_advisory(1) == 1
    # Unknown / unparseable ratings -> None.
    assert _norm_advisory(None) is None
    assert _norm_advisory("") is None
    assert _norm_advisory("explicit") is None
    assert _norm_advisory("4") is None


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
