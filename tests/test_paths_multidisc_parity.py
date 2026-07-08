"""render_filename and build_destination must agree on what counts as
multidisc. With disc_total > 1 but no disc number, the filename previously
used the multidisc template (rendering {disc} as a constant 1) while no
Disc N folder was created — giving every disc's tracks colliding names.
"""
from dragontag.app.config import UserSettings
from dragontag.app.library import paths
from dragontag.app.tagging.schema import TrackTags


def _settings_with_disc_prefix():
    return UserSettings(
        filename_template_single="{track:02d}. {title}.{ext}",
        filename_template_multidisc="{disc}-{track:02d}. {title}.{ext}",
    )


def test_missing_disc_number_uses_single_template(monkeypatch):
    monkeypatch.setattr(paths, "settings", _settings_with_disc_prefix)
    tags = TrackTags(title="T", track=1, disc=None, disc_total=2)

    name = paths.render_filename(tags, ".flac")

    assert name == "01. T.flac"  # no bogus constant "1-" disc prefix


def test_real_multidisc_uses_disc_prefix_and_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "settings", _settings_with_disc_prefix)
    tags = TrackTags(
        title="T", track=1, disc=2, disc_total=2,
        album="Al", album_artist_display="Art",
    )

    dest = paths.build_destination(tags, ".flac", library_root=tmp_path)

    assert dest.name == "2-01. T.flac"
    assert dest.parent.name == "Disc 2"


def test_missing_disc_number_gets_no_disc_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "settings", _settings_with_disc_prefix)
    tags = TrackTags(
        title="T", track=1, disc=None, disc_total=2,
        album="Al", album_artist_display="Art",
    )

    dest = paths.build_destination(tags, ".flac", library_root=tmp_path)

    assert dest.parent.name == "Al"
    assert dest.name == "01. T.flac"
