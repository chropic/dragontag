"""Local cover-art fallback: a transient CAA outage must not clog the review
queue when a usable cover is sitting next to the file.

``coverart.find_local_cover`` looks for a sidecar image, then art embedded in
the track itself, then in a sibling album track. The pipeline calls it from the
``cover_fetch_failed`` branch of ``_commit_tag_path`` before routing to review.
"""
import io
import wave
from pathlib import Path

import pytest

from dragontag.app.db import session
from dragontag.app.ingest import pipeline
from dragontag.app.models import Job, JobStatus, ReviewReason
from dragontag.app.tagging import coverart
from dragontag.app.tagging.schema import TrackTags


def _png_bytes(color=(200, 30, 30)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (10, 10), color).save(out, format="PNG")
    return out.getvalue()


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def _make_flac(path: Path, *, with_cover: bool = False) -> None:
    from mutagen.flac import FLAC, Picture

    magic = b"fLaC"
    hdr = bytes([0x80]) + (34).to_bytes(3, "big")
    sr, ch, bps = 44100, 2, 16
    packed = (sr << 44) | ((ch - 1) << 41) | ((bps - 1) << 36) | 0
    streaminfo = (
        (4096).to_bytes(2, "big") + (4096).to_bytes(2, "big")
        + (0).to_bytes(3, "big") + (0).to_bytes(3, "big")
        + packed.to_bytes(8, "big") + b"\x00" * 16
    )
    path.write_bytes(magic + hdr + streaminfo)
    if with_cover:
        f = FLAC(str(path))
        pic = Picture()
        pic.type = 3
        pic.mime = "image/png"
        pic.data = _png_bytes()
        f.add_picture(pic)
        f.save()


# ---- find_local_cover ----

def test_sidecar_cover_found(tmp_path):
    (tmp_path / "cover.png").write_bytes(_png_bytes())
    src = tmp_path / "song.wav"
    _make_wav(src)
    cover = coverart.find_local_cover(src)
    assert cover is not None
    assert cover.mime in ("image/png", "image/jpeg")


def test_sidecar_name_case_insensitive(tmp_path):
    (tmp_path / "Folder.JPG").write_bytes(_png_bytes())  # jpg ext, png bytes → re-encoded
    src = tmp_path / "song.wav"
    _make_wav(src)
    assert coverart.find_local_cover(src) is not None


def test_embedded_sibling_cover_found(tmp_path):
    src = tmp_path / "01.wav"
    _make_wav(src)
    sib = tmp_path / "02.flac"
    _make_flac(sib, with_cover=True)
    cover = coverart.find_local_cover(src, [sib])
    assert cover is not None


def test_no_local_cover_returns_none(tmp_path):
    src = tmp_path / "song.wav"
    _make_wav(src)
    assert coverart.find_local_cover(src, []) is None


def test_embedded_cover_reads_flac_picture(tmp_path):
    p = tmp_path / "t.flac"
    _make_flac(p, with_cover=True)
    assert coverart._embedded_cover(p) is not None
    p2 = tmp_path / "t2.flac"
    _make_flac(p2, with_cover=False)
    assert coverart._embedded_cover(p2) is None


# ---- pipeline wiring: fetch failure + local cover → no review ----

def _review_job(src: Path) -> int:
    with session() as s:
        j = Job(source_path=str(src), original_name=src.name, status=JobStatus.needs_review)
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def test_commit_uses_local_cover_instead_of_review(tmp_path, monkeypatch):
    """When the CAA fetch raises but a sidecar cover exists, the job must embed
    the local cover and proceed past the cover stage rather than parking in
    ``needs_review`` with ``cover_fetch_failed``."""
    import requests
    from dragontag.app import net
    from dragontag.app.tagging import snapshot

    d = tmp_path / "album"
    d.mkdir()
    src = d / "song.wav"
    _make_wav(src)
    (d / "cover.png").write_bytes(_png_bytes())
    jid = _review_job(src)

    def boom(url, **kw):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(net.requests, "get", boom)

    # Stop right after the cover stage so the test needs no real library/move.
    def stop(*a, **k):
        raise RuntimeError("reached-write-stage")

    monkeypatch.setattr(snapshot, "capture", stop)

    tags = TrackTags(title="T", artist_display="A", mb_album_id="rel-id")
    with session() as s:
        job = s.get(Job, jid)
        with pytest.raises(RuntimeError, match="reached-write-stage"):
            pipeline._commit_tag_path(s, job, src, tags, score=0.9)

    assert tags.cover_bytes  # local sidecar cover was embedded
    with session() as s:
        job = s.get(Job, jid)
        assert job.review_reason != ReviewReason.cover_fetch_failed
