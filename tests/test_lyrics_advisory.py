"""Tests for lyrics/advisory schema rendering and explicit classifier."""
from dragontag.app.config import Separators
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.advisory import is_explicit, strip_lrc_timestamps


# ---------------------------------------------------------------------------
# TrackTags.to_vorbis() — lyrics & advisory fields
# ---------------------------------------------------------------------------

def test_lyrics_in_vorbis():
    sep = Separators()
    t = TrackTags(title="test", lyrics="Some lyrics here")
    out = t.to_vorbis(sep)
    assert out["LYRICS"] == "Some lyrics here"


def test_advisory_clean_in_vorbis():
    sep = Separators()
    t = TrackTags(title="test", advisory=0)
    out = t.to_vorbis(sep)
    assert out["ITUNESADVISORY"] == "0"


def test_advisory_explicit_in_vorbis():
    sep = Separators()
    t = TrackTags(title="test", advisory=1)
    out = t.to_vorbis(sep)
    assert out["ITUNESADVISORY"] == "1"


def test_lyrics_and_advisory_omitted_when_none():
    sep = Separators()
    t = TrackTags(title="test")
    out = t.to_vorbis(sep)
    assert "LYRICS" not in out
    assert "ITUNESADVISORY" not in out


# ---------------------------------------------------------------------------
# advisory.is_explicit()
# ---------------------------------------------------------------------------

def test_explicit_word_detected():
    assert is_explicit("I don't give a fuck about that") is True


def test_clean_lyrics_not_flagged():
    assert is_explicit("I love you so much, baby, always and forever") is False


def test_lrc_timestamps_stripped_before_check():
    lrc = "[00:12.34] hell yeah\n[00:15.00] this is clean"
    assert is_explicit(lrc) is False


def test_explicit_word_inside_lrc_timestamps_detected():
    lrc = "[00:10.00] holy shit this song\n[00:14.00] is pretty good"
    assert is_explicit(lrc) is True


def test_word_boundary_respected():
    # "classic" contains "ass" but should NOT match because it's not a standalone word
    assert is_explicit("a classic performance tonight") is False


def test_strip_lrc_timestamps():
    text = "[01:23.45] Some line\n[02:00.00] Another line"
    stripped = strip_lrc_timestamps(text)
    assert "[" not in stripped
    assert "Some line" in stripped
    assert "Another line" in stripped
