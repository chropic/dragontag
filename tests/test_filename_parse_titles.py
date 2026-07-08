"""The filename parser must strip real track-number prefixes without eating
numbers that open the actual title ("7 Years", "99 Luftballons").

A bare-space separator is only trusted after a zero-padded number — natural
numbers followed by a space are far more likely to be part of the title.
"""
from pathlib import Path

from dragontag.app.identify.filename_parse import parse


def _title(name: str) -> str | None:
    return parse(Path(f"/x/{name}"))["title"]


def test_punctuated_prefixes_are_stripped():
    assert _title("01 - Title.flac") == "Title"
    assert _title("01. Title.flac") == "Title"
    assert _title("14-Title.flac") == "Title"
    assert _title("3) Title.flac") == "Title"


def test_zero_padded_bare_space_prefix_is_stripped():
    assert _title("01 Title.flac") == "Title"
    assert _title("07 Some Song.flac") == "Some Song"


def test_numeric_titles_survive():
    assert _title("7 Years.flac") == "7 Years"
    assert _title("99 Luftballons.flac") == "99 Luftballons"
    assert _title("100 Years.flac") == "100 Years"
    assert _title("22 A Million.flac") == "22 A Million"
    assert _title("2 Become 1.flac") == "2 Become 1"


def test_artist_title_split_still_works_after_prefix():
    got = parse(Path("/x/01 - Artist - Title.flac"))
    assert got["artist"] == "Artist"
    assert got["title"] == "Title"
