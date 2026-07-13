"""Release-consensus candidate selection: near-tied candidates must converge
on one deterministic release instead of scattering an album's tracks across
editions (the album-splitting bug)."""
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.identify.musicbrainz import Candidate
from dragontag.app.ingest.pipeline import (
    _CONSENSUS_EPSILON,
    _existing_release_for_group,
    _select_candidate,
)
from dragontag.app.models import Track


def _entry(total, release_id, *, status="Official", track_count=10, rg="rg-1"):
    cand = Candidate(
        score=total,
        recording_id="rec-1",
        release_id=release_id,
        raw_release={
            "id": release_id,
            "status": status,
            "track-count": track_count,
            "release-group": {"id": rg},
        },
    )
    return (total, None, cand)


def test_clear_winner_is_untouched():
    scored = [_entry(0.95, "rel-a"), _entry(0.80, "rel-b", track_count=99)]
    assert _select_candidate(scored)[2].release_id == "rel-a"


def test_epsilon_boundary_excludes_distant_candidates():
    # 0.06 below the top is outside the 0.05 window — must not win even with
    # a better preference profile.
    scored = [
        _entry(0.95, "rel-a", status="Bootleg", track_count=1),
        _entry(0.95 - _CONSENSUS_EPSILON - 0.01, "rel-b", track_count=99),
    ]
    assert _select_candidate(scored)[2].release_id == "rel-a"


def test_official_release_preferred_within_epsilon():
    scored = [
        _entry(0.95, "rel-boot", status="Bootleg"),
        _entry(0.94, "rel-official", status="Official"),
    ]
    assert _select_candidate(scored)[2].release_id == "rel-official"


def test_larger_edition_preferred_then_lexicographic_id():
    scored = [
        _entry(0.95, "rel-b", track_count=20),
        _entry(0.95, "rel-a", track_count=29),
    ]
    assert _select_candidate(scored)[2].release_id == "rel-a"
    # Equal track counts: smallest release id wins deterministically.
    scored = [_entry(0.95, "rel-b"), _entry(0.95, "rel-a")]
    assert _select_candidate(scored)[2].release_id == "rel-a"


def test_library_majority_release_beats_track_count(tmp_path):
    with session() as s:
        for i in range(2):
            s.add(Track(path=str(tmp_path / f"{i}.flac"),
                        mb_release_group_id="rg-1", mb_album_id="rel-small"))
        s.commit()
    try:
        assert _existing_release_for_group("rg-1") == "rel-small"
        assert _existing_release_for_group(None) is None
        assert _existing_release_for_group("rg-unknown") is None
        # A big edition scores a hair higher, but two library tracks already
        # sit on rel-small — new tracks must join them, not fork the album.
        scored = [
            _entry(0.95, "rel-big", track_count=29),
            _entry(0.94, "rel-small", track_count=20),
        ]
        assert _select_candidate(scored)[2].release_id == "rel-small"
    finally:
        with session() as s:
            for t in s.exec(select(Track).where(Track.mb_release_group_id == "rg-1")).all():
                s.delete(t)
            s.commit()
