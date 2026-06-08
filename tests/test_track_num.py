from dragontag.app.identify.musicbrainz import _strip_track_num


def test_strips_genuine_track_number_prefixes():
    assert _strip_track_num("01. Title") == "Title"
    assert _strip_track_num("1. Title") == "Title"
    assert _strip_track_num("14-Track") == "Track"
    assert _strip_track_num("03 - Song") == "Song"
    assert _strip_track_num("7) Intro") == "Intro"


def test_preserves_numbers_that_are_part_of_the_title():
    # A leading number with no '.', '-', or ')' separator is real title text,
    # not a track-number prefix — these must survive untouched.
    assert _strip_track_num("99 Luftballons") == "99 Luftballons"
    assert _strip_track_num("7 Years") == "7 Years"
    assert _strip_track_num("2 Become 1") == "2 Become 1"
    assert _strip_track_num("100 Years") == "100 Years"


def test_leaves_non_numeric_titles_unchanged():
    assert _strip_track_num("Song Title") == "Song Title"
