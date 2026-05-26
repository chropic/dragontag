from aio_tagger.app.identify.scoring import score_candidate


def test_perfect_match_scores_high():
    sb = score_candidate(
        candidate_recording={
            "title": "deletee (intro)",
            "artist-credit": [{"artist": {"name": "Bladee"}}],
            "length": 90000,
        },
        candidate_release={"title": "gluee"},
        clues={
            "title": "deletee (intro)", "artist": "Bladee",
            "album": "gluee", "duration": 90.0,
        },
        mb_search_score=1.0,
    )
    assert sb.total > 0.95


def test_wrong_title_scores_low():
    sb = score_candidate(
        candidate_recording={"title": "completely unrelated", "artist-credit": [], "length": 200000},
        candidate_release={"title": "other album"},
        clues={"title": "deletee (intro)", "artist": "Bladee", "album": "gluee", "duration": 90.0},
        mb_search_score=0.1,
    )
    assert sb.total < 0.4
