"""`write_tags` must not mutate the caller's TrackTags.

Regression for the bug where `skip_fields` zeroing happened in place, so the
pipeline (which builds the library destination from the *same* object right
after writing) misfiled tracks — e.g. skipping `disc_total` collapsed a
multi-disc album into a single-disc path.
"""
import dataclasses
import wave
from pathlib import Path

import dragontag.app.tagging.writers as writers_pkg
from dragontag.app.config import UserSettings
from dragontag.app.library.paths import build_destination
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.writers import write_tags


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def _multidisc_tags() -> TrackTags:
    return TrackTags(
        title="Song",
        album="Album",
        album_artist_display="Artist",
        track=1,
        track_total=10,
        disc=1,
        disc_total=2,
    )


def test_write_tags_does_not_mutate_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(writers_pkg, "settings", lambda: UserSettings(skip_fields=["disc_total"]))
    p = tmp_path / "t.wav"
    _make_wav(p)
    tags = _multidisc_tags()
    before = dataclasses.asdict(tags)

    write_tags(p, tags)

    assert dataclasses.asdict(tags) == before   # caller object untouched
    assert tags.disc_total == 2


def test_destination_unaffected_by_skip_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(writers_pkg, "settings", lambda: UserSettings(skip_fields=["disc_total"]))
    p = tmp_path / "t.wav"
    _make_wav(p)
    tags = _multidisc_tags()

    write_tags(p, tags)
    dest = build_destination(tags, ".wav")

    # disc_total survived → still routed into a multi-disc folder.
    assert "Disc " in str(dest)


def test_skipped_field_is_omitted_from_file(tmp_path, monkeypatch):
    from mutagen.wave import WAVE

    monkeypatch.setattr(writers_pkg, "settings", lambda: UserSettings(skip_fields=["genres"]))
    p = tmp_path / "t.wav"
    _make_wav(p)

    write_tags(p, TrackTags(title="Song", genres=["Rock"]))

    assert WAVE(str(p)).tags.getall("TCON") == []   # genre genuinely skipped


def test_no_skip_fields_is_a_noop(tmp_path, monkeypatch):
    from mutagen.wave import WAVE

    monkeypatch.setattr(writers_pkg, "settings", lambda: UserSettings(skip_fields=[]))
    p = tmp_path / "t.wav"
    _make_wav(p)
    tags = TrackTags(title="Song", genres=["Rock"])

    write_tags(p, tags)

    assert tags.genres == ["Rock"]
    assert WAVE(str(p)).tags.getall("TCON")[0].text == ["Rock"]
