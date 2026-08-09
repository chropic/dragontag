"""Cover-art capping + ID3 sort-frame regressions (Findings 2, 4, 5).

WAV is used because the stdlib ``wave`` module synthesizes a valid file without
an external encoder, and mutagen writes the same ID3 frames into WAV as MP3
(both go through ``populate_id3``).
"""
import wave
from io import BytesIO
from pathlib import Path

from PIL import Image

from dragontag.app.config import Separators
from dragontag.app.tagging import partial
from dragontag.app.tagging.schema import TrackTags
from dragontag.app.tagging.writers import _id3common
from dragontag.app.tagging.writers import flac as flac_writer
from dragontag.app.tagging.writers.wav import write as wav_write


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def _img_bytes(px: int, fmt: str) -> bytes:
    out = BytesIO()
    Image.new("RGB", (px, px), (120, 30, 200)).save(out, format=fmt)
    return out.getvalue()


def test_id3_sort_frames_not_double_written(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)
    tags = TrackTags(
        title="x",
        artists=["A"],
        artist_sort=["Surname, A"],
        album_artists=["AA"],
        album_artist_sort=["AA Sort"],
    )
    wav_write(p, tags, Separators())

    audio = WAVE(str(p))
    assert audio.tags.getall("TSOP")[0].text == ["Surname, A"]   # artist sort
    assert audio.tags.getall("TSO2")[0].text == ["AA Sort"]      # album-artist sort
    # The dedicated frames above are the only place sort names go — no TXXX dupes.
    assert audio.tags.getall("TXXX:ARTISTSORT") == []
    assert audio.tags.getall("TXXX:ALBUMARTISTSORT") == []


def test_cap_cover_uppercase_png_stays_png():
    data, mime = _id3common._cap_cover(_img_bytes(1600, "PNG"), "image/PNG")
    assert mime == "image/png"
    assert Image.open(BytesIO(data)).format == "PNG"


def test_uppercase_png_mime_is_treated_as_png(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)
    # >1200px forces the re-encode path where the format decision is made.
    tags = TrackTags(title="x", cover_bytes=_img_bytes(1600, "PNG"), cover_mime="image/PNG")
    wav_write(p, tags, Separators())

    apic = WAVE(str(p)).tags.getall("APIC")[0]
    assert apic.mime == "image/png"                                   # not mis-encoded as JPEG
    assert max(Image.open(BytesIO(apic.data)).size) <= 1200


def test_wav_retag_replaces_existing_embedded_cover(tmp_path):
    """A review re-tag must replace, not stack or retain, pipeline artwork."""
    from mutagen.wave import WAVE

    p = tmp_path / "retag.wav"
    _make_wav(p)
    old_cover = _img_bytes(300, "JPEG")
    wav_write(
        p,
        TrackTags(title="Old match", cover_bytes=old_cover, cover_mime="image/jpeg"),
        Separators(),
    )

    new_cover = BytesIO()
    Image.new("RGB", (400, 400), (10, 220, 40)).save(new_cover, format="PNG")
    new_cover_bytes = new_cover.getvalue()
    wav_write(
        p,
        TrackTags(title="Reviewed match", cover_bytes=new_cover_bytes, cover_mime="image/png"),
        Separators(),
    )

    apics = WAVE(str(p)).tags.getall("APIC")
    assert len(apics) == 1
    assert apics[0].mime == "image/png"
    assert apics[0].data == new_cover_bytes
    assert apics[0].data != old_cover


def test_partial_write_cover_caps_oversized_art(tmp_path):
    from mutagen.wave import WAVE

    p = tmp_path / "t.wav"
    _make_wav(p)
    partial.write_cover(p, _img_bytes(2000, "JPEG"), "image/jpeg")

    apic = WAVE(str(p)).tags.getall("APIC")[0]
    assert max(Image.open(BytesIO(apic.data)).size) <= 1200


def test_cap_cover_single_canonical_source():
    # flac.py no longer carries its own copy — all writers share _id3common's.
    assert flac_writer._cap_cover is _id3common._cap_cover
