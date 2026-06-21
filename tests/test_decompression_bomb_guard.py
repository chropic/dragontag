"""F12: a small byte stream can declare an enormous pixel grid (decompression
bomb). Both cover-art decode sites must reject the declared size *before*
calling convert()/thumbnail()/save(), which is what actually allocates the
full decoded pixel buffer.
"""
from io import BytesIO

import pytest

from dragontag.app.tagging import coverart
from dragontag.app.tagging.writers import _id3common


class _FakeHugeImage:
    """Stands in for a PIL Image whose *declared* size is enormous, without
    actually allocating any pixel data — mirrors what Image.open() does
    before .load()/.convert() is called on a real bomb."""

    format = "PNG"
    size = (50_000, 50_000)  # 2.5 billion pixels

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def convert(self, *a, **kw):
        raise AssertionError("must not decode a declared-oversized image")

    def thumbnail(self, *a, **kw):
        raise AssertionError("must not decode a declared-oversized image")


def test_cap_cover_skips_decode_for_oversized_declared_dimensions(monkeypatch):
    monkeypatch.setattr("PIL.Image.open", lambda *_a, **_kw: _FakeHugeImage())
    data = b"not a real image but bytes nonetheless"
    out_data, out_mime = _id3common._cap_cover(data, "image/png")
    # Oversized declared image: bail out untouched rather than decode.
    assert out_data == data
    assert out_mime == "image/png"


def test_pick_and_download_skips_candidate_with_oversized_declared_dimensions(monkeypatch):
    import dragontag.app.net as net

    class _Resp:
        status_code = 200
        headers: dict = {}

    monkeypatch.setattr(net, "fetch_bytes", lambda *a, **kw: (_Resp(), b"fake-bytes"))
    monkeypatch.setattr("PIL.Image.open", lambda *_a, **_kw: _FakeHugeImage())

    art = coverart._pick_and_download([{"image": "http://x/cover.png"}])
    # The only candidate is rejected for an oversized declared pixel grid,
    # so no cover is returned (not a crash / huge allocation).
    assert art is None
