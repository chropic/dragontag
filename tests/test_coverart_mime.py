"""Cover art is always stored as genuine JPEG or PNG.

Regression: a non-JPEG/PNG image from CAA (GIF/WEBP/BMP/TIFF) was embedded with
a wrong declared ``image/jpeg`` MIME, so the bytes and MIME disagreed.
"""
from io import BytesIO

import pytest
from PIL import Image

from dragontag.app.tagging import coverart


class _Resp:
    """Minimal stand-in for the streaming ``requests.Response`` used by net.fetch_bytes."""

    def __init__(self, content, status=200):
        self._content = content
        self.status_code = status
        self.headers: dict = {}

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")

    def close(self):
        pass


def _img(fmt: str, px: int = 100) -> bytes:
    out = BytesIO()
    Image.new("RGB", (px, px), (10, 20, 30)).save(out, format=fmt)
    return out.getvalue()


def _patch_get(monkeypatch, content):
    # Cover-art downloads now go through net.fetch_bytes, which calls
    # requests.get inside the net module — patch it there.
    from dragontag.app import net

    monkeypatch.setattr(net.requests, "get", lambda url, **kw: _Resp(content))


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


# ---- metadata fetch: soft-miss vs. real failure ----
#
# A 404 from CAA means "no art here" and must stay a soft-miss (None). A 5xx or
# a transport error (the archive.org mirror flaking with 500 / SSL failures seen
# in production) is transient and must *surface* as a requests exception so the
# pipeline can route the job to review for retry rather than embedding no art.

def test_caa_404_is_soft_miss(monkeypatch):
    from dragontag.app import net
    monkeypatch.setattr(net.requests, "get", lambda url, **kw: _Resp(b"", status=404))
    assert coverart.fetch_for_release("some-mbid") is None


def test_caa_500_raises(monkeypatch):
    import requests
    from dragontag.app import net
    monkeypatch.setattr(net.requests, "get", lambda url, **kw: _Resp(b"", status=500))
    with pytest.raises(requests.HTTPError):
        coverart.fetch_for_release("some-mbid")


def test_caa_ssl_error_propagates(monkeypatch):
    import requests
    from dragontag.app import net

    def boom(url, **kw):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(net.requests, "get", boom)
    with pytest.raises(requests.exceptions.SSLError):
        coverart.fetch_for_release("some-mbid")
