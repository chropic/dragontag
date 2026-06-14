"""AcoustID fingerprint lookup.

Used as the last-resort identifier when MB text search returns nothing
(e.g. a file with no usable filename and no existing tags). Computes a
Chromaprint fingerprint via the ``fpcalc`` binary (installed in the Docker
image) and asks the AcoustID API which MB recording IDs it matches.

The API requires an application key — we read it from the
``DRAGONTAG_ACOUSTID_KEY_FILE`` Docker secret. If no key is configured, this module
quietly returns an empty list and the pipeline routes the job to review.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import acoustid

from ..config import env, settings

log = logging.getLogger(__name__)


@dataclass
class AcoustIDMatch:
    acoustid_id: str   # the AcoustID itself (not the MB id). May be empty.
    score: float        # 0..1, how close the fingerprint matches
    recording_id: str | None  # MB recording UUID we should look up next


def lookup(path: Path) -> list[AcoustIDMatch]:
    """Return zero or more matches in descending confidence order.

    Returns ``[]`` on any of: no API key, no ``fpcalc`` available, file
    can't be fingerprinted, network/API failure. All of those are non-fatal
    — the pipeline falls through to the review queue with reason ``no_match``.
    """
    key = env().resolve_acoustid_key()
    if not key:
        return []
    try:
        duration, fingerprint = acoustid.fingerprint_file(str(path))
    except acoustid.NoBackendError:
        # fpcalc isn't installed / on PATH. Shouldn't happen in our Docker
        # image (we apt-get libchromaprint-tools) but possible in local dev.
        return []
    except acoustid.FingerprintGenerationError:
        # File is too short / corrupt / not actually audio.
        return []

    try:
        response = acoustid.lookup(
            key, fingerprint, duration, meta="recordings",
            timeout=settings().network_timeout_seconds,
        )
    except Exception:
        # WebServiceError, socket timeouts, and any other network/parse failure
        # are all non-fatal — fall through to the review queue rather than
        # erroring (and never let a raw exception escape into the pipeline).
        log.debug("AcoustID lookup failed", exc_info=True)
        return []

    out: list[AcoustIDMatch] = []
    for result in response.get("results") or []:
        aid = result.get("id", "")
        score = float(result.get("score", 0))
        for recording in result.get("recordings") or []:
            rid = recording.get("id")
            if rid:
                out.append(AcoustIDMatch(acoustid_id=aid, score=score, recording_id=rid))
    out.sort(key=lambda x: x.score, reverse=True)
    return out
