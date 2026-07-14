from pathlib import Path

from dragontag.app.library import paths as paths_mod
from dragontag.app.library.paths import (
    artist_fold_key,
    build_destination,
    fold_text,
    primary_artist,
    sanitize_segment,
)
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


# --- fold keys -------------------------------------------------------------


def test_fold_text_case_punctuation_unicode(monkeypatch):
    _patch_seps(monkeypatch, "")
    # casefold
    assert fold_text("LUCKI") == fold_text("Lucki")
    assert fold_text("BONES") == fold_text("Bones")
    # curly vs straight apostrophe
    assert fold_text("Her's") == fold_text("Her’s")
    assert fold_text("Pi'erre Bourne") == fold_text("Pi’erre Bourne")
    # U+2010 hyphen vs ASCII hyphen
    assert fold_text("Tay-K") == fold_text("Tay‐K")
    # ® stripped
    assert fold_text("NIGO®") == fold_text("Nigo")
    # × folds to x
    assert fold_text("A × B") == fold_text("A x B")
    # whitespace collapse
    assert fold_text("Until  Japan") == fold_text("Until Japan")


def test_fold_text_negatives(monkeypatch):
    _patch_seps(monkeypatch, "")
    # genuinely different names must not collide
    assert fold_text("Bones") != fold_text("Bone")
    assert fold_text("AC/DC") == "ac/dc"  # slash preserved, not treated as separator


def test_artist_fold_key_strips_feat(monkeypatch):
    _patch_seps(monkeypatch, "")
    # primary_artist runs first, then the fold
    assert artist_fold_key("Drake feat. Rihanna") == artist_fold_key("Drake")
    assert artist_fold_key("fakemink") == artist_fold_key("Fakemink")


# --- build_destination existing-folder reuse (prevention) ------------------


def test_build_destination_reuses_existing_case_variant_dir(tmp_path):
    # An existing 'afraid' dir + tags spelling 'Afraid' → path lands under the
    # existing 'afraid', not a new 'Afraid'.
    (tmp_path / "afraid" / "Album").mkdir(parents=True)
    t = TrackTags(
        title="Song", artist_display="Afraid", album="Album",
        album_artist_display="Afraid", track=1, track_total=1, disc=1, disc_total=1,
    )
    dest = build_destination(t, ".flac", library_root=tmp_path)
    assert dest.parts[-3:-1] == ("afraid", "Album")


def test_build_destination_no_existing_dir_uses_tag_casing(tmp_path):
    t = TrackTags(
        title="Song", artist_display="Afraid", album="Album",
        album_artist_display="Afraid", track=1, track_total=1, disc=1, disc_total=1,
    )
    dest = build_destination(t, ".flac", library_root=tmp_path)
    assert dest.parts[-3:-1] == ("Afraid", "Album")


def test_build_destination_reuse_still_guards_root_escape(tmp_path):
    # The traversal defence must still fire after segment substitution.
    import pytest
    t = TrackTags(
        title="Song", artist_display="../../etc", album="x",
        album_artist_display="../../etc", track=1, track_total=1, disc=1, disc_total=1,
    )
    # sanitize_segment neutralizes separators, so this should NOT escape — but
    # the resolved path must remain under the root regardless.
    dest = build_destination(t, ".flac", library_root=tmp_path)
    dest.resolve().relative_to(tmp_path.resolve())
