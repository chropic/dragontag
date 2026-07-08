"""Punctuation-spacing grammar rule must not explode initialisms."""
from dragontag.app.tagging.formatter import _fix_punctuation_spacing, apply


def test_initialisms_survive_punct_spacing():
    assert _fix_punctuation_spacing("R.E.M.") == "R.E.M."
    assert _fix_punctuation_spacing("U.S.A.") == "U.S.A."
    assert _fix_punctuation_spacing("a.k.a. Slim") == "a.k.a. Slim"


def test_missing_space_after_word_punctuation_still_fixed():
    assert _fix_punctuation_spacing("Hello,World") == "Hello, World"
    assert _fix_punctuation_spacing("End.Start") == "End. Start"


def test_apply_grammar_keeps_band_names_intact():
    got = apply("Losing My Religion by R.E.M.", grammar=True)
    assert "R.E.M." in got
