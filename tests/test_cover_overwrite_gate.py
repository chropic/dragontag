"""write_cover_jpg's overwrite gate must actually protect an existing higher-
resolution cover — the incoming width is compared against the existing file's
width, not just the static floor.
"""
from io import BytesIO

from PIL import Image

from dragontag.app.library.mover import write_cover_jpg


def _jpeg(width: int, height: int = 100) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, format="JPEG")
    return buf.getvalue()


def test_smaller_fetch_does_not_clobber_larger_existing(tmp_path):
    existing = _jpeg(3000)
    (tmp_path / "cover.jpg").write_bytes(existing)

    got = write_cover_jpg(tmp_path, _jpeg(1000), min_overwrite_pixels=500, new_width=1000)

    assert got is None
    assert (tmp_path / "cover.jpg").read_bytes() == existing


def test_below_floor_is_still_rejected(tmp_path):
    existing = _jpeg(300)
    (tmp_path / "cover.jpg").write_bytes(existing)

    got = write_cover_jpg(tmp_path, _jpeg(400), min_overwrite_pixels=500, new_width=400)

    assert got is None
    assert (tmp_path / "cover.jpg").read_bytes() == existing


def test_larger_fetch_overwrites(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(_jpeg(800))
    new = _jpeg(1200)

    got = write_cover_jpg(tmp_path, new, min_overwrite_pixels=500, new_width=1200)

    assert got is not None
    assert (tmp_path / "cover.jpg").read_bytes() == new


def test_user_supplied_art_always_writes(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(_jpeg(3000))
    new = _jpeg(200)

    # new_width=0 is the explicit user-chosen-art convention.
    got = write_cover_jpg(tmp_path, new, min_overwrite_pixels=0, new_width=0)

    assert got is not None
    assert (tmp_path / "cover.jpg").read_bytes() == new


def test_no_existing_cover_writes(tmp_path):
    new = _jpeg(400)
    got = write_cover_jpg(tmp_path, new, min_overwrite_pixels=500, new_width=400)
    assert got is not None
    assert (tmp_path / "cover.jpg").read_bytes() == new
