from dragontag.app.identify.artist_split import split_multi_artist


def test_no_separator_passthrough():
    assert split_multi_artist("Daft Punk") == ["Daft Punk"]


def test_feat_variants_split():
    assert split_multi_artist("2hollis feat. nate sib") == ["2hollis", "nate sib"]
    assert split_multi_artist("Drake ft. Rihanna") == ["Drake", "Rihanna"]
    assert split_multi_artist("Drake featuring Rihanna") == ["Drake", "Rihanna"]


def test_ampersand_splits():
    assert split_multi_artist("Earth, Wind & Fire") == ["Earth", "Wind", "Fire"]


def test_tyler_the_creator_not_split():
    assert split_multi_artist("Tyler, The Creator") == ["Tyler, The Creator"]


def test_diplo_sidepiece_splits():
    assert split_multi_artist("Diplo, SIDEPIECE") == ["Diplo", "SIDEPIECE"]


def test_multiple_commas_independently_guarded():
    assert split_multi_artist("A, The Roots, B") == ["A, The Roots", "B"]


def test_empty_and_none():
    assert split_multi_artist("") == []
    assert split_multi_artist(None) == []


def test_whitespace_trimmed():
    assert split_multi_artist("A  ,  B") == ["A", "B"]
