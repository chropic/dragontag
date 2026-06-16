"""Outbound HTTP helpers with SSRF guards and response size caps.

Two failure modes this module exists to prevent:

* **SSRF** — when a URL originates from user input (e.g. a cover-art URL pasted
  into the review UI), a naive ``requests.get`` lets a caller probe internal
  services (``http://127.0.0.1``, cloud metadata at ``169.254.169.254``, RFC1918
  hosts, ``file://`` …). ``validate_public_url`` resolves the host and refuses
  any address that isn't publicly routable. Pair it with ``allow_redirects=False``
  so a 30x can't bounce the request onto an internal target.

* **OOM** — a malicious or compromised upstream can stream gigabytes into memory.
  ``fetch_bytes`` reads the body in chunks and aborts past ``max_bytes``.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests

# Generous default — large enough for original cover-art scans and API JSON,
# small enough that a hostile server can't exhaust the worker's memory.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB


class UnsafeURLError(ValueError):
    """Raised when a URL uses a disallowed scheme or resolves to a private host."""


def _host_is_blocked(host: str) -> bool:
    """True if *host* resolves to any non-publicly-routable address.

    Resolving up front (rather than trusting the literal host) also blunts the
    obvious ``http://127.0.0.1`` / RFC1918 / link-local probes. It is not a full
    DNS-rebinding defence (the address could change between this check and the
    socket connect), which is why callers fetching untrusted URLs also disable
    redirects and cap the body.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # unresolvable → treat as unsafe
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def validate_public_url(url: str) -> None:
    """Raise :class:`UnsafeURLError` unless *url* is an http(s) URL to a public host."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"scheme {parsed.scheme!r} is not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if _host_is_blocked(host):
        raise UnsafeURLError(f"host {host!r} resolves to a non-public address")


def read_capped(resp: requests.Response, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """Read a streaming response body, aborting once it exceeds *max_bytes*."""
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise UnsafeURLError(f"response too large: {declared} bytes > {max_bytes}")
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise UnsafeURLError(f"response exceeded {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int = DEFAULT_MAX_BYTES,
    validate: bool = True,
    allow_redirects: bool = True,
    headers: dict | None = None,
    params: dict | None = None,
) -> tuple[requests.Response, bytes]:
    """GET *url* and return ``(response, body)`` with a hard size cap.

    Pass ``validate=True`` (default) for user-supplied URLs to enforce the
    SSRF guard; pass ``validate=False`` for fetches to trusted, hard-coded API
    hosts. ``raise_for_status`` is intentionally *not* called — callers inspect
    ``response.status_code`` themselves (several upstreams use 404 as a soft miss).
    """
    if validate:
        validate_public_url(url)
    resp = requests.get(
        url,
        timeout=timeout,
        stream=True,
        allow_redirects=allow_redirects,
        headers=headers,
        params=params,
    )
    try:
        body = read_capped(resp, max_bytes)
    finally:
        resp.close()
    return resp, body
