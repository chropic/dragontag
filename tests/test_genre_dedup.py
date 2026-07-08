"""Genre whitelist matching is hyphen/space-agnostic, so dedup must be too —
"Hip Hop" and "Hip-Hop" are one genre and must not both survive the filter.
"""
from dragontag.app.identify.genres import filter_genres


def test_hyphen_and_space_spellings_dedup_to_first_seen():
    assert filter_genres(["Hip Hop", "Hip-Hop", "Rock"]) == ["Hip Hop", "Rock"]


def test_fallback_path_dedups_the_same_way():
    # Nothing whitelisted -> junk-filtered fallback, same dedup rule.
    got = filter_genres(["Weird Unlisted", "weird-unlisted"])
    assert got == ["Weird Unlisted"]
