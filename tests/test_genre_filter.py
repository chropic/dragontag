"""Genre whitelist filtering (identify/genres.py)."""
from dragontag.app.identify.genres import filter_genres, load_whitelist


def test_whitelist_loads():
    wl = load_whitelist()
    assert len(wl) > 1000
    assert "rock" in wl
    assert "hip hop" in wl


def test_junk_dropped_when_real_genres_present():
    out = filter_genres(["Billboard Top 100", "rock", "seen live", "indie rock"])
    assert out == ["rock", "indie rock"]


def test_hyphen_space_normalization():
    # whitelist has "hip hop"; both spellings should pass
    assert filter_genres(["Hip-Hop"]) == ["Hip-Hop"]
    assert filter_genres(["hip hop"]) == ["hip hop"]


def test_fallback_keeps_non_junk_when_nothing_whitelisted():
    out = filter_genres(["zorblax core", "billboard top 100", "best of 2011"])
    assert out == ["zorblax core"]


def test_all_junk_yields_empty():
    assert filter_genres(["seen live", "top 40 charts"]) == []


def test_dedupes_preserving_order():
    out = filter_genres(["Rock", "rock", "Jazz"])
    assert out == ["Rock", "Jazz"]
