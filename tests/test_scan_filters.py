"""Tests for library/filters.py and its integration with scanner + bulk."""
from pathlib import Path

import pytest

from dragontag.app.library.filters import is_path_excluded


def test_filter_pattern_matches_filename():
    p = Path("/library/Artist/Album/Thumbs.db")
    assert is_path_excluded(p, [r"Thumbs\.db$"], [])


def test_filter_pattern_no_match():
    p = Path("/library/Artist/Album/01. Song.flac")
    assert not is_path_excluded(p, [r"Thumbs\.db$", r"\.ini$"], [])


def test_filter_extension_pattern():
    p = Path("/library/Artist/Album/desktop.ini")
    assert is_path_excluded(p, [r"\.ini$"], [])


def test_exclude_dir_exact():
    p = Path("/library/Private/secret.flac")
    assert is_path_excluded(p, [], ["/library/Private"])


def test_exclude_dir_subpath():
    p = Path("/library/Private/SubDir/track.flac")
    assert is_path_excluded(p, [], ["/library/Private"])


def test_exclude_dir_no_match():
    p = Path("/library/Public/track.flac")
    assert not is_path_excluded(p, [], ["/library/Private"])


def test_invalid_regex_is_skipped(caplog):
    import logging
    p = Path("/library/track.flac")
    with caplog.at_level(logging.DEBUG, logger="dragontag.app.library.filters"):
        result = is_path_excluded(p, [r"[invalid"], [])
    assert not result


def test_both_empty():
    p = Path("/library/Artist/Album/track.flac")
    assert not is_path_excluded(p, [], [])


def test_exclude_dir_trailing_slash():
    """Trailing slash on exclude dir should still match."""
    p = Path("/library/Private/track.flac")
    assert is_path_excluded(p, [], ["/library/Private/"])


def test_exclude_file_exact():
    p = Path("/drop/keep-me.flac")
    assert is_path_excluded(p, [], [], [str(p)])


def test_exclude_file_no_match():
    p = Path("/drop/other.flac")
    assert not is_path_excluded(p, [], [], [str(Path("/drop/keep-me.flac"))])
