"""Cover art is always stored as genuine JPEG or PNG.

Regression: a non-JPEG/PNG image from CAA (GIF/WEBP/BMP/TIFF) was embedded with
a wrong declared ``image/jpeg`` MIME, so the bytes and MIME disagreed.
"""
from io import BytesIO

from PIL import Image

from dragontag.app.tagging import coverart


class _Resp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status


def _img(fmt: str, px: int = 100) -> bytes:
    out = BytesIO()
    Image.new("RGB", (px, px), (10, 20, 30)).save(out, format=fmt)
    return out.getvalue()


def _patch_get(monkeypatch, content):
    monkeypatch.setattr(coverart.requests, "get", lambda url, timeout=20: _Resp(content))


def test_gif_cover_reencoded_to_jpeg(monkeypatch):
    _patch_get(monkeypatch, _img("GIF"))
    art = coverart._pick_and_download([{"image": "http://x/cover.gif"}])
    assert art is not None
    assert art.mime == "image/jpeg"
    assert Image.open(BytesIO(art.data)).format == "JPEG"


def test_png_cover_stays_png(monkeypatch):
    _patch_get(monkeypatch, _img("PNG"))
    art = coverart._pick_and_download([{"image": "http://x/cover.png"}])
    assert art is not None
    assert art.mime == "image/png"
    assert Image.open(BytesIO(art.data)).format == "PNG"


def test_jpeg_cover_stays_jpeg(monkeypatch):
    _patch_get(monkeypatch, _img("JPEG"))
    art = coverart._pick_and_download([{"image": "http://x/cover.jpg"}])
    assert art is not None
    assert art.mime == "image/jpeg"
    assert Image.open(BytesIO(art.data)).format == "JPEG"
