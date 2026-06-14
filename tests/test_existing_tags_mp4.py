"""MP4 ``trkn``/``disk`` tuples must normalize so the scanner can parse them.

Regression: ``existing_tags`` used to hand the scanner the tuple's repr
(``"(5, 12)"``), which ``_parse_num``/``_parse_total`` couldn't parse, so every
scanned M4A/MP4 ended up with NULL track/disc numbers.
"""
from dragontag.app.identify.existing_tags import _coerce
from dragontag.app.library.scanner import _parse_num, _parse_total


class _Frame:
    """Minimal stand-in for a mutagen ID3 text frame."""

    def __init__(self, text):
        self.text = text


def test_coerce_mp4_trkn_tuple():
    assert _coerce([(5, 12)]) == "5/12"


def test_coerce_mp4_disk_tuple():
    assert _coerce([(1, 2)]) == "1/2"


def test_coerce_plain_vorbis_list():
    assert _coerce(["Hello"]) == "Hello"


def test_coerce_bare_value():
    assert _coerce("plain") == "plain"


def test_coerce_id3_frame():
    assert _coerce(_Frame(["Title"])) == "Title"


def test_coerce_empty_frame_is_none():
    assert _coerce(_Frame([])) is None


def test_scanner_parses_normalized_mp4_track():
    s = _coerce([(5, 12)])      # what read() now stores under "track"
    assert _parse_num(s) == 5
    assert _parse_total(s) == 12


def test_scanner_parses_normalized_mp4_disc():
    s = _coerce([(1, 2)])
    assert _parse_num(s) == 1
    assert _parse_total(s) == 2
