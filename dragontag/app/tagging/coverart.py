"""Cover art fetcher — queries the MusicBrainz Cover Art Archive.

We try the release-level endpoint first (most specific) and fall back to the
release-group endpoint if the release has no images of its own. CAA returns
JSON that lists each image with a set of pre-rendered thumbnail URLs plus
the original. We always prefer the original (highest fidelity), and only
fall back to thumbnails if the original 404s or is too slow.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import requests

_CAA_BASE = "https://coverartarchive.org"


@dataclass
class CoverArt:
    """Fetched cover image + metadata used downstream for embed/sidecar decisions."""

    data: bytes
    mime: str  # "image/jpeg" or "image/png"
    width: int
    height: int


def _get_json(url: str, timeout: float = 10.0):
    r = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
    if r.status_code == 404:
        # No coverage in CAA — treat as soft-miss, not an error.
        return None
    r.raise_for_status()
    return r.json()


def _pick_and_download(images: list[dict]) -> CoverArt | None:
    """Given CAA's ``images`` list (already filtered to ``front``), download
    the best version available. Returns ``None`` if every URL fails.

    Strategy: take the first front-flagged image (CAA orders by quality) and
    try its `image` (original) first, then progressively smaller thumbnails.
    Falling back to thumbnails matters because the originals are sometimes
    enormous TIFFs that other tools can't read.
    """
    if not images:
        return None
    img = images[0]

    candidates: list[str] = []
    if img.get("image"):
        candidates.append(img["image"])
    thumbs = img.get("thumbnails", {}) or {}
    for k in ("1200", "large", "500", "small", "250"):
        if k in thumbs:
            candidates.append(thumbs[k])

    for url in candidates:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                continue
            data = r.content
            # Probe dimensions/mime via Pillow so we can store them for the
            # ``cover.jpg`` overwrite policy. The downstream writers (and the
            # MP4 ``covr`` atom) only understand JPEG and PNG, so anything else
            # CAA might serve (GIF/WEBP/BMP/TIFF) is re-encoded to JPEG here —
            # otherwise the bytes wouldn't match the declared MIME.
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    w, h = im.size
                    if im.format == "PNG":
                        mime = "image/png"
                    elif im.format in ("JPEG", "MPO"):
                        mime = "image/jpeg"
                    else:
                        out = io.BytesIO()
                        im.convert("RGB").save(out, format="JPEG", quality=90)
                        data = out.getvalue()
                        mime = "image/jpeg"
            except Exception:
                # Non-image response (unlikely but defend against it).
                w = h = 0
                mime = "image/jpeg"
            return CoverArt(data=data, mime=mime, width=w, height=h)
        except requests.RequestException:
            continue
    return None


def fetch_for_release(release_mbid: str) -> CoverArt | None:
    """Try the release-specific cover. Returns ``None`` if CAA has none."""
    meta = _get_json(f"{_CAA_BASE}/release/{release_mbid}")
    if not meta:
        return None
    fronts = [img for img in meta.get("images", []) if img.get("front")]
    return _pick_and_download(fronts)


def fetch_for_release_group(rg_mbid: str) -> CoverArt | None:
    """Fall back to the release-group cover (shared across all releases)."""
    meta = _get_json(f"{_CAA_BASE}/release-group/{rg_mbid}")
    if not meta:
        return None
    fronts = [img for img in meta.get("images", []) if img.get("front")]
    return _pick_and_download(fronts)
