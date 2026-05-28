from dragontag.app.tagging.formatter import apply, apply_grammar


def test_all_caps_with_contractions_and_possessives():
    out = apply_grammar("I DONT LIKE PEOPLES SHIT")
    assert "Don't" in out
    assert "People's" in out
    # Not shouting anymore
    assert out != out.upper()


def test_contractions_preserve_case():
    assert apply_grammar("i dont know") == "i don't know"
    assert "Don't" in apply_grammar("I Dont Know")
    # ALL-CAPS strings are de-shouted before contractions are inserted, so the
    # contraction comes back in Title Case.
    assert "Don't" in apply_grammar("I DONT KNOW")


def test_punctuation_spacing():
    assert apply_grammar("hello ,world") == "hello, world"
    assert apply_grammar("hi  there") == "hi there"


def test_apply_runs_grammar_when_enabled():
    out = apply("I DONT LIKE PEOPLES SHIT", grammar=True)
    assert "Don't" in out and "People's" in out


def test_apply_idempotent():
    once = apply_grammar("I DONT LIKE PEOPLES SHIT")
    twice = apply_grammar(once)
    assert once == twice


def test_apply_grammar_empty():
    assert apply_grammar("") == ""
    assert apply_grammar(None) is None
