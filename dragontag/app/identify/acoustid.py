"""AcoustID fingerprint lookup.

Used as the last-resort identifier when MB text search returns nothing
(e.g. a file with no usable filename and no existing tags). Computes a
Chromaprint fingerprint via the ``fpcalc`` binary (installed in the Docker
image) and asks the AcoustID API which MB recording IDs it matches.

The API requires an application key — we read it from the
``DRAGONTAG_ACOUSTID_KEY_FILE`` Docker secret. If no key is configured, this module
quietly returns an empty list and the pipeline routes the job to review.

Fingerprinting itself is bounded by ``fingerprint_timeout_seconds``: pyacoustid's
own fpcalc invocation has no timeout, so a hung/corrupt file would otherwise
block the single ingest worker thread forever.
"""
from __future__ import annotations

import errno
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import acoustid
from acoustid import FingerprintGenerationError, NoBackendError

from ..config import env, settings

log = logging.getLogger(__name__)

# fpcalc's own env var name, replicated from pyacoustid so a custom fpcalc
# path set via FPCALC keeps working when we bypass pyacoustid's invocation.
_FPCALC_ENVVAR = "FPCALC"
_FPCALC_COMMAND = "fpcalc"


@dataclass
class AcoustIDMatch:
    acoustid_id: str   # the AcoustID itself (not the MB id). May be empty.
    score: float        # 0..1, how close the fingerprint matches
    recording_id: str | None  # MB recording UUID we should look up next


def _fingerprint_file_with_timeout(path: Path, *, maxlength: int, timeout: float) -> tuple[float, bytes]:
    """Reimplementation of ``acoustid._fingerprint_file_fpcalc`` with a hard
    wall-clock timeout. pyacoustid's own ``Popen().communicate()`` has no
    timeout parameter, so a hung/corrupt file can block the single ingest
    worker thread forever. Raises ``acoustid.NoBackendError`` if fpcalc isn't
    on PATH, ``acoustid.FingerprintGenerationError`` on a timeout, non-zero
    exit, or malformed output — matching pyacoustid's own exception contract.
    """
    fpcalc = os.environ.get(_FPCALC_ENVVAR, _FPCALC_COMMAND)
    command = [fpcalc, "-length", str(maxlength), str(path)]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise NoBackendError("fpcalc not found") from exc
        raise FingerprintGenerationError(f"fpcalc invocation failed: {exc!s}") from exc

    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()  # reap the zombie after kill()
        raise FingerprintGenerationError(
            f"fpcalc timed out after {timeout}s on {path.name}"
        ) from None

    if proc.poll():
        raise FingerprintGenerationError(f"fpcalc exited with status {proc.poll()}")

    duration = fp = None
    for line in output.splitlines():
        parts = line.split(b"=", 1)
        if len(parts) != 2:
            raise FingerprintGenerationError("malformed fpcalc output")
        if parts[0] == b"DURATION":
            try:
                duration = float(parts[1])
            except ValueError:
                raise FingerprintGenerationError("fpcalc duration not numeric") from None
        elif parts[0] == b"FINGERPRINT":
            fp = parts[1]
    if duration is None or fp is None:
        raise FingerprintGenerationError("missing fpcalc output")
    return duration, fp


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
        duration, fingerprint = _fingerprint_file_with_timeout(
            path, maxlength=acoustid.MAX_AUDIO_LENGTH,
            timeout=settings().fingerprint_timeout_seconds,
        )
    except acoustid.NoBackendError:
        # fpcalc isn't installed / on PATH. Shouldn't happen in our Docker
        # image (we apt-get libchromaprint-tools) but possible in local dev.
        return []
    except acoustid.FingerprintGenerationError:
        # File is too short / corrupt / not actually audio, or fpcalc hung
        # past fingerprint_timeout_seconds and was killed.
        log.warning("fingerprint failed for %s", path, exc_info=True)
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
        # Logged at warning (not debug) so a systemic problem — bad API key,
        # AcoustID outage, persistent rate-limiting — is actually visible
        # instead of silently masquerading as "no match" for every file.
        log.warning("AcoustID lookup failed for %s", path, exc_info=True)
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
