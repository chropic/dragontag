from pathlib import Path

from aio_tagger.app.library.paths import sanitize_segment, build_destination
from aio_tagger.app.tagging.schema import TrackTags


def test_sanitize_strips_only_forbidden():
    assert sanitize_segment("foo:bar?") == "foo_bar_"
    assert sanitize_segment("hello (world)") == "hello (world)"
    # Trailing dots/spaces removed (Windows-safe)
    assert sanitize_segment("name.") == "name"
    assert sanitize_segment("  ok  ") == "ok"
    # Empty -> placeholder
    assert sanitize_segment("...") == "_"
    # Unicode preserved
    assert sanitize_segment("café") == "café"


def test_build_destination_single_disc(monkeypatch):
    t = TrackTags(
        title="deletee (intro)",
        artist_display="Bladee//Thaiboy Digital",
        album="gluee",
        album_artist_display="Bladee",
        track=1, track_total=9, disc=1, disc_total=1,
    )
    dest = build_destination(t, ".flac")
    assert dest.parts[-3:] == ("Bladee", "gluee", "01. deletee (intro).flac")


def test_build_destination_multi_disc():
    t = TrackTags(
        title="Track",
        album="DoubleAlbum",
        album_artist_display="Artist",
        track=3, track_total=10, disc=2, disc_total=2,
    )
    dest = build_destination(t, ".flac")
    assert dest.parts[-4:] == ("Artist", "DoubleAlbum", "Disc 2", "03. Track.flac")
