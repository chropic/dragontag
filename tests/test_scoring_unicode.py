"""L4/L5: scoring is unicode-form-insensitive and treats duration 0 as valid."""
import unicodedata

from dragontag.app.identify.scoring import _sim, score_candidate


def test_sim_matches_across_unicode_forms():
    composed = "Café"                                    # NFC, single é
    decomposed = unicodedata.normalize("NFD", "Café")    # e + combining acute
    assert composed != decomposed                        # genuinely different bytes
    assert _sim(composed, decomposed) == 1.0


def test_sim_is_casefolded():
    assert _sim("STRASSE", "strasse") == 1.0


def test_zero_duration_participates():
    # src_dur == 0 and candidate length == 0 → perfect duration match (1.0),
    # not silently dropped as it would be under a truthiness check.
    sb = score_candidate(
        candidate_recording={"title": "x", "length": 0},
        candidate_release={"title": "a"},
        clues={"title": "x", "duration": 0.0},
    )
    assert sb.duration == 1.0


def test_non_numeric_length_does_not_raise():
    sb = score_candidate(
        candidate_recording={"title": "x", "length": "garbage"},
        candidate_release={"title": "a"},
        clues={"title": "x", "duration": 100.0},
    )
    assert sb.duration == 0.0
