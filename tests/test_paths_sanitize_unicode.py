"""Unicode normalization in sanitize_segment.

Generated folder/file names must not carry exotic dash/quote codepoints or
invisible characters (they break ASCII lookups and confuse SMB clients), but
diacritics and non-Latin scripts are intentional and must survive untouched.
"""
import unicodedata

from dragontag.app.library.paths import sanitize_segment


def test_dashes_normalized_to_ascii_hyphen():
    # U+2010 ‐, U+2011 ‑, U+2012 ‒, U+2013 –, U+2014 —, U+2212 −
    assert sanitize_segment("Tay‐K") == "Tay-K"
    assert sanitize_segment("Tay‑K") == "Tay-K"
    assert sanitize_segment("A‒B") == "A-B"
    assert sanitize_segment("Friends– x") == "Friends- x"
    assert sanitize_segment("A—B") == "A-B"
    assert sanitize_segment("A−B") == "A-B"


def test_curly_quotes_normalized():
    assert sanitize_segment("Don’t Be Dumb") == "Don't Be Dumb"
    assert sanitize_segment("‘quoted’") == "'quoted'"
    # Curly double quotes normalize to '"', which is Windows-forbidden -> "_".
    assert sanitize_segment("“Album”") == "_Album_"


def test_zero_width_and_soft_hyphen_stripped():
    assert sanitize_segment("Fake​mink") == "Fakemink"
    assert sanitize_segment("A‌‍⁠B") == "AB"
    assert sanitize_segment("﻿Lead") == "Lead"
    assert sanitize_segment("Cus­tom") == "Custom"


def test_diacritics_and_scripts_preserved():
    for name in ("Aminé", "Lyfë", "AftërLyfe", "Barry Künzel", "café",
                 "宇多田ヒカル", "Варг", "The Dø"):
        assert sanitize_segment(name) == name


def test_nfc_only_no_compatibility_folding():
    # NFD input recomposes to NFC...
    decomposed = unicodedata.normalize("NFD", "Aminé")
    assert sanitize_segment(decomposed) == "Aminé"
    # ...but compatibility characters are NOT folded (that would mangle names).
    assert sanitize_segment("Varg²™") == "Varg²™"


def test_windows_reserved_device_names_defused():
    assert sanitize_segment("CON") == "CON_"
    assert sanitize_segment("con") == "con_"
    assert sanitize_segment("NUL.data") == "NUL.data_"
    assert sanitize_segment("COM7") == "COM7_"
    assert sanitize_segment("LPT1") == "LPT1_"
    # Names merely *containing* a reserved word are fine.
    assert sanitize_segment("CONAN") == "CONAN"
    assert sanitize_segment("Comfort") == "Comfort"


def test_existing_behavior_unchanged():
    assert sanitize_segment("foo:bar?") == "foo_bar_"
    assert sanitize_segment("name.") == "name"
    assert sanitize_segment("...") == "_"
    assert sanitize_segment("") == "_"
