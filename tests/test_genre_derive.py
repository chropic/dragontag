"""derive_genres: rank MB community tags by vote count, filter junk, cap, case.

Also guards the release-group fallback shape used by ingest and the Fix-genres
action — a recording tagged only with junk must derive to nothing so the caller
can fall back to the release-group.
"""
from dragontag.app.identify.musicbrainz import derive_genres


def test_empty_input_yields_empty():
    assert derive_genres([]) == []


def test_ranks_by_vote_count_and_titlecases():
    out = derive_genres(
        [{"name": "pop", "count": "2"}, {"name": "rock", "count": "9"}]
    )
    assert out == ["Rock", "Pop"]  # rock outvotes pop; default casing is title


def test_junk_tags_dropped():
    out = derive_genres(
        [{"name": "seen live", "count": "500"}, {"name": "rock", "count": "1"}]
    )
    assert out == ["Rock"]


def test_all_junk_derives_to_empty_so_caller_can_fall_back():
    # This is the fallback trigger: recording had tags, but none survive.
    assert derive_genres([{"name": "billboard top 100", "count": "40"}]) == []
