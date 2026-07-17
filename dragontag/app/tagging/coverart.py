"""Cover art fetcher — queries the MusicBrainz Cover Art Archive.

We try the release-level endpoint first (most specific) and fall back to the
release-group endpoint if the release has no images of its own. CAA returns
JSON that lists each image with a set of pre-rendered thumbnail URLs plus
the original. We always prefer the original (highest fidelity), and only
fall back to thumbnails if the original 404s or is too slow.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass

import requests

from ..net import fetch_bytes

_CAA_BASE = "https://coverartarchive.org"

# Hard cap on a single cover-art download so a malicious/compromised upstream
# can't stream gigabytes into the worker's memory.
_IMAGE_MAX_BYTES = 32 * 1024 * 1024  # 32 MiB
_JSON_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB
# Decompression-bomb guard: a small byte stream can still declare an enormous
# pixel grid. Check the (cheap, header-only) declared size before decoding
# further with convert()/save(), which allocate the full pixel buffer.
_MAX_DECODE_PIXELS = 40_000_000


@dataclass
class CoverArt:
    """Fetched cover image + metadata used downstream for embed/sidecar decisions."""

    data: bytes
    mime: str  # "image/jpeg" or "image/png"
    width: int
    height: int


def _get_json(url: str, timeout: float = 10.0):
    # Trusted host (hard-coded CAA base) → skip SSRF validation, but still cap
    # the response so a misbehaving upstream can't balloon memory.
    r, body = fetch_bytes(
        url,
        timeout=timeout,
        max_bytes=_JSON_MAX_BYTES,
        validate=False,
        allow_redirects=True,  # trusted host; CAA answers via redirects
        headers={"Accept": "application/json"},
    )
    if r.status_code == 404:
        # No coverage in CAA — treat as soft-miss, not an error.
        return None
    r.raise_for_status()
    return json.loads(body)


def _probe_image(data: bytes) -> CoverArt | None:
    """Validate/normalise raw image bytes into a ``CoverArt``.

    Shared by the CAA download path and the local-fallback path: applies the
    decompression-bomb guard and re-encodes anything that isn't already JPEG/PNG
    (the only formats the writers + MP4 ``covr`` atom understand). Returns
    ``None`` when the bytes aren't a usable image.
    """
    if not data:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            if w * h > _MAX_DECODE_PIXELS:
                return None
            if im.format == "PNG":
                return CoverArt(data=data, mime="image/png", width=w, height=h)
            if im.format in ("JPEG", "MPO"):
                return CoverArt(data=data, mime="image/jpeg", width=w, height=h)
            out = io.BytesIO()
            im.convert("RGB").save(out, format="JPEG", quality=90)
            return CoverArt(data=out.getvalue(), mime="image/jpeg", width=w, height=h)
    except Exception:
        return None


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
            # CAA image URLs redirect to archive.org mirrors, so redirects stay
            # enabled; the size cap still bounds memory use.
            r, data = fetch_bytes(
                url, timeout=20, max_bytes=_IMAGE_MAX_BYTES, validate=False,
                allow_redirects=True,
            )
            if r.status_code != 200:
                continue
            # Probe dimensions/mime via Pillow so we can store them for the
            # ``cover.jpg`` overwrite policy. The downstream writers (and the
            # MP4 ``covr`` atom) only understand JPEG and PNG, so anything else
            # CAA might serve (GIF/WEBP/BMP/TIFF) is re-encoded to JPEG here —
            # otherwise the bytes wouldn't match the declared MIME.
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    w, h = im.size
                    if w * h > _MAX_DECODE_PIXELS:
                        # Declared pixel grid is absurd for cover art — skip
                        # this candidate rather than risk a decode-time
                        # decompression-bomb allocation.
                        continue
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
        except (requests.RequestException, ValueError):
            # Network error or oversized response → try the next candidate URL.
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


# Sidecar image basenames to look for in an album directory, in preference
# order. Matched case-insensitively against the stem (extension ignored).
_SIDECAR_STEMS = ("cover", "folder", "front", "album", "albumart", "albumartsmall")
_SIDECAR_EXTS = (".jpg", ".jpeg", ".png")


def _embedded_cover(path) -> CoverArt | None:
    """Extract front-cover bytes already embedded in an audio file.

    Format-agnostic: FLAC ``PICTURE`` blocks (and the Vorbis
    ``metadata_block_picture`` base64 variant), ID3 ``APIC`` frames (MP3/WAV),
    and MP4 ``covr`` atoms. Returns ``None`` when the file carries no usable art.
    """
    import base64

    try:
        import mutagen
        from mutagen.flac import FLAC, Picture

        f = mutagen.File(str(path))
    except Exception:
        return None
    if f is None:
        return None

    # FLAC: native picture blocks (prefer front cover, type 3).
    pics = getattr(f, "pictures", None)
    if pics:
        chosen = next((p for p in pics if getattr(p, "type", None) == 3), pics[0])
        cover = _probe_image(bytes(chosen.data))
        if cover:
            return cover

    tags = getattr(f, "tags", None)
    if tags is None:
        return None

    # ID3 (MP3/WAV): APIC frames, front cover (type 3) preferred.
    getall = getattr(tags, "getall", None)
    if callable(getall):
        try:
            apics = getall("APIC")
        except Exception:
            apics = []
        if apics:
            chosen = next((a for a in apics if getattr(a, "type", None) == 3), apics[0])
            cover = _probe_image(bytes(chosen.data))
            if cover:
                return cover

    # MP4: covr atom (a list of MP4Cover, which are bytes subclasses).
    try:
        covr = tags.get("covr")
    except Exception:
        covr = None
    if covr:
        cover = _probe_image(bytes(covr[0]))
        if cover:
            return cover

    # Vorbis (Ogg and any non-FLAC Vorbis container): base64 picture block.
    try:
        b64 = tags.get("metadata_block_picture")
    except Exception:
        b64 = None
    if b64:
        try:
            pic = Picture(base64.b64decode(b64[0]))
            cover = _probe_image(bytes(pic.data))
            if cover:
                return cover
        except Exception:
            pass
    return None


def find_local_cover(src, sibling_paths=()) -> CoverArt | None:
    """Find cover art locally when the remote CAA fetch is unavailable.

    Looks, in order, for: a sidecar image file (``cover.jpg``/``folder.jpg``/…)
    in ``src``'s directory, then art already embedded in ``src`` itself, then
    art embedded in any sibling album track in ``sibling_paths``. Used as a
    fallback so a transient Cover Art Archive outage doesn't clog the review
    queue with retriable ``cover_fetch_failed`` items when a perfectly good
    cover is sitting right next to the file.

    ``src`` and ``sibling_paths`` are ``pathlib.Path`` (or path-like). Returns
    ``None`` only when nothing usable is found anywhere locally.
    """
    from pathlib import Path

    src = Path(src)

    # 1) Sidecar image in the source directory.
    directory = src.parent
    try:
        entries = list(directory.iterdir())
    except OSError:
        entries = []
    by_name = {p.name.lower(): p for p in entries if p.is_file()}
    for stem in _SIDECAR_STEMS:
        for ext in _SIDECAR_EXTS:
            hit = by_name.get(stem + ext)
            if hit is None:
                continue
            try:
                cover = _probe_image(hit.read_bytes())
            except OSError:
                cover = None
            if cover:
                return cover

    # 2) Art embedded in the file itself, then in each sibling track.
    seen = set()
    for cand in (src, *[Path(p) for p in sibling_paths]):
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not cand.is_file():
                continue
        except OSError:
            continue
        cover = _embedded_cover(cand)
        if cover:
            return cover
    return None
