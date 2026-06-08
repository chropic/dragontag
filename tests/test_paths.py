from pathlib import Path

from dragontag.app.library import paths as paths_mod
from dragontag.app.library.paths import sanitize_segment, build_destination, primary_artist
from dragontag.app.tagging.schema import TrackTags


class _FakeSettings:
    def __init__(self, seps=""):
        self.folder_artist_split_separators = seps


def _patch_seps(monkeypatch, seps):
    monkeypatch.setattr(paths_mod, "settings", lambda: _FakeSettings(seps))


def test_primary_artist_strips_feat(monkeypatch):
    # feat./ft./featuring guests are always stripped, regardless of separators.
    _patch_seps(monkeypatch, "")
    assert primary_artist("Drake feat. Rihanna") == "Drake"
    assert primary_artist("Drake ft. Rihanna") == "Drake"
    assert primary_artist("Drake featuring Rihanna") == "Drake"
    assert primary_artist("Calvin Harris (feat. Dua Lipa)") == "Calvin Harris"
    # Names that merely contain the letters are untouched.
    assert primary_artist("Daft Punk") == "Daft Punk"


def test_primary_artist_no_split_by_default(monkeypatch):
    # With no configured separators, multi-artist credits stay intact.
    _patch_seps(monkeypatch, "")
    assert primary_artist("Tyler, The Creator") == "Tyler, The Creator"
    assert primary_artist("Earth, Wind & Fire") == "Earth, Wind & Fire"
    assert primary_artist("Bladee//Thaiboy Digital") == "Bladee//Thaiboy Digital"


def test_primary_artist_split_opt_in(monkeypatch):
    # Opting in to "&,;" reduces collaborations to the first artist...
    _patch_seps(monkeypatch, "&,;")
    assert primary_artist("A & B") == "A"
    assert primary_artist("Earth, Wind & Fire") == "Earth"
    # ...but slashes are never split, even when opted in.
    assert primary_artist("AC/DC") == "AC/DC"
    assert primary_artist("Bladee//Thaiboy Digital") == "Bladee//Thaiboy Digital"


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


def test_build_destination_falls_back_to_stripped_artist():
    # No album_artist (e.g. a scanned file without ALBUMARTIST): the artist
    # credit is used, but feat. guests are still stripped from the folder.
    # Uses real settings (default = no multi-artist split) so render_filename
    # still has its template fields.
    t = TrackTags(
        title="Song",
        artist_display="Main Artist feat. Guest",
        album="Album",
        album_artist_display=None,
        track=1, track_total=1, disc=1, disc_total=1,
    )
    dest = build_destination(t, ".flac")
    assert dest.parts[-3:] == ("Main Artist", "Album", "01. Song.flac")


def test_build_destination_multi_disc():
    t = TrackTags(
        title="Track",
        album="DoubleAlbum",
        album_artist_display="Artist",
        track=3, track_total=10, disc=2, disc_total=2,
    )
    dest = build_destination(t, ".flac")
    assert dest.parts[-4:] == ("Artist", "DoubleAlbum", "Disc 2", "03. Track.flac")
