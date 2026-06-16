"""SSRF guard + response size cap in app.net."""
import pytest

from dragontag.app import net


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:6379/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",  # RFC1918
        "http://192.168.1.1/",
        "http://[::1]/",  # IPv6 loopback
        "file:///etc/passwd",  # disallowed scheme
        "ftp://example.com/x",  # disallowed scheme
        "https:///nohost",  # missing host
    ],
)
def test_validate_public_url_rejects_unsafe(url):
    with pytest.raises(net.UnsafeURLError):
        net.validate_public_url(url)


def test_validate_public_url_allows_public_host(monkeypatch):
    # Host resolving to a routable public address should pass (no live DNS).
    monkeypatch.setattr(
        net.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    net.validate_public_url("https://example.com/release/abc")


def test_validate_public_url_blocks_host_resolving_to_private(monkeypatch):
    # DNS-rebinding style: public name, private answer → blocked.
    monkeypatch.setattr(
        net.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))],
    )
    with pytest.raises(net.UnsafeURLError):
        net.validate_public_url("https://evil.example.com/")


class _Resp:
    def __init__(self, chunks, headers=None, status=200):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status

    def iter_content(self, chunk_size=65536):
        yield from self._chunks

    def close(self):
        pass


def test_read_capped_aborts_oversized(monkeypatch):
    big = [b"x" * 1024] * 100  # 100 KiB streamed
    resp = _Resp(big)
    with pytest.raises(net.UnsafeURLError):
        net.read_capped(resp, max_bytes=10 * 1024)


def test_read_capped_honors_content_length(monkeypatch):
    resp = _Resp([b"x"], headers={"Content-Length": str(50 * 1024 * 1024)})
    with pytest.raises(net.UnsafeURLError):
        net.read_capped(resp, max_bytes=1024)


def test_read_capped_returns_body_within_limit():
    resp = _Resp([b"abc", b"def"])
    assert net.read_capped(resp, max_bytes=1024) == b"abcdef"
