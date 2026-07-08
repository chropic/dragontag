"""The feat-split regex consumes the opening bracket of "(feat. X)" — the
matching closer must not survive on the featured artist's name.
"""
from dragontag.app.identify.artist_split import split_multi_artist


def test_parenthesized_feat_drops_trailing_bracket():
    assert split_multi_artist("Calvin Harris (feat. Dua Lipa)") == [
        "Calvin Harris", "Dua Lipa",
    ]


def test_square_bracket_feat_drops_trailing_bracket():
    assert split_multi_artist("A [ft. B]") == ["A", "B"]


def test_balanced_brackets_in_names_are_kept():
    # The closer pairs with an opener inside the piece — not the eaten one.
    assert split_multi_artist("A feat. B (UK)") == ["A", "B (UK)"]


def test_plain_feat_still_splits():
    assert split_multi_artist("2hollis feat. nate sib") == ["2hollis", "nate sib"]
