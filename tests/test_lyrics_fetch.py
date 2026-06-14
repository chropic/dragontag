"""The LRCLIB /search fallback must only accept a hit that actually matches.

Regression: the fallback took ``hits[0]`` unconditionally, so a near-miss could
embed a different song's lyrics (and skew the explicit classifier).
"""
from dragontag.app.tagging.lyrics_fetcher import _hit_matches


def test_exact_match_accepted():
    assert _hit_matches({"trackName": "Song", "artistName": "Artist"}, "Artist", "Song")


def test_match_is_case_and_whitespace_insensitive():
    assert _hit_matches({"trackName": "  song ", "artistName": "ARTIST"}, "artist", "Song")


def test_wrong_track_rejected():
    assert not _hit_matches({"trackName": "Other", "artistName": "Artist"}, "Artist", "Song")


def test_wrong_artist_rejected():
    assert not _hit_matches({"trackName": "Song", "artistName": "Nope"}, "Artist", "Song")


def test_missing_fields_rejected():
    assert not _hit_matches({}, "Artist", "Song")
