"""M1: artist-credit extraction must tolerate malformed/partial MB payloads.

Previously the list builders did ``c["artist"]["name"]``/``["id"]`` directly,
raising KeyError on an artist dict that lacked those keys and needlessly
bouncing a tag-able file to review.
"""
from dragontag.app.identify.musicbrainz import (
    _credit_ids,
    _credit_names,
    _credit_sorts,
)

_GOOD = [
    {"artist": {"name": "Bladee", "sort-name": "Bladee", "id": "abc"}},
    {"joinphrase": " & "},
    {"artist": {"name": "Ecco2k", "id": "def"}},  # no sort-name
]
_MALFORMED = [
    {"joinphrase": " feat. "},          # no artist key
    {"artist": {}},                      # artist dict missing name/id
    {"artist": {"name": "Thaiboy"}},     # name only, no id
    "raw string credit",                # not a dict at all
]


def test_names_skip_nameless_and_nondict():
    assert _credit_names(_GOOD) == ["Bladee", "Ecco2k"]
    assert _credit_names(_MALFORMED) == ["Thaiboy"]


def test_sorts_fall_back_to_name():
    assert _credit_sorts(_GOOD) == ["Bladee", "Ecco2k"]
    assert _credit_sorts(_MALFORMED) == ["Thaiboy"]


def test_ids_skip_missing():
    assert _credit_ids(_GOOD) == ["abc", "def"]
    assert _credit_ids(_MALFORMED) == []  # none have an id


def test_empty_credits():
    assert _credit_names([]) == []
    assert _credit_sorts([]) == []
    assert _credit_ids([]) == []


def test_names_splits_unsplit_feat_credit():
    creds = [{"artist": {"name": "2hollis feat. nate sib", "id": "abc"}}]
    assert _credit_names(creds) == ["2hollis", "nate sib"]
