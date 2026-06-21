"""fpcalc fingerprinting must never hang the ingest worker forever."""
import subprocess
import sys

import acoustid
import pytest

from dragontag.app.identify import acoustid as acid_mod


class _FakeEnv:
    def resolve_acoustid_key(self):
        return "fake-key"


class _FakeSettings:
    def __init__(self, fingerprint_timeout_seconds=0.2):
        self.fingerprint_timeout_seconds = fingerprint_timeout_seconds
        self.network_timeout_seconds = 1.0


def _patch(monkeypatch, timeout=0.2):
    monkeypatch.setattr(acid_mod, "env", lambda: _FakeEnv())
    monkeypatch.setattr(acid_mod, "settings", lambda: _FakeSettings(timeout))


def test_fingerprint_timeout_seconds_default():
    from dragontag.app.config import UserSettings

    assert UserSettings().fingerprint_timeout_seconds == 30.0


def test_lookup_returns_empty_on_real_fpcalc_hang(monkeypatch, tmp_path):
    # A real subprocess that sleeps far longer than the configured timeout,
    # proving the Popen/communicate(timeout=...) wiring actually bounds it
    # rather than just exercising mocked exception-handling logic.
    script = tmp_path / "slow_fpcalc.py"
    script.write_text(
        "import sys, time\n"
        "time.sleep(5)\n"
        "print('DURATION=1.0')\n"
        "print('FINGERPRINT=AQ')\n"
    )
    fake_fpcalc = tmp_path / "fpcalc"
    fake_fpcalc.write_text(f"#!/bin/sh\nexec {sys.executable} {script}\n")
    fake_fpcalc.chmod(0o755)
    monkeypatch.setenv("FPCALC", str(fake_fpcalc))

    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"\x00")

    _patch(monkeypatch, timeout=0.5)

    import time

    start = time.monotonic()
    result = acid_mod.lookup(audio)
    elapsed = time.monotonic() - start

    assert result == []
    assert elapsed < 4.0  # bounded by the timeout, not the 5s sleep


def test_lookup_returns_empty_when_fpcalc_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("FPCALC", str(tmp_path / "does-not-exist"))
    _patch(monkeypatch)
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"\x00")
    assert acid_mod.lookup(audio) == []


def test_network_lookup_failure_logs_at_warning(monkeypatch, tmp_path, caplog):
    """S6: a network/API failure during the AcoustID lookup step must be
    visible (warning), not buried at debug — otherwise a bad key or an
    outage looks identical to "no match" with no operator-visible signal."""
    _patch(monkeypatch)
    monkeypatch.setattr(
        acid_mod, "_fingerprint_file_with_timeout", lambda *a, **k: (1.0, b"AQ")
    )

    def boom(*a, **k):
        raise RuntimeError("simulated AcoustID API outage")

    monkeypatch.setattr(acid_mod.acoustid, "lookup", boom)

    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"\x00")

    with caplog.at_level("WARNING", logger=acid_mod.log.name):
        result = acid_mod.lookup(audio)

    assert result == []
    assert any("AcoustID lookup failed" in r.message for r in caplog.records)


def test_fingerprint_file_with_timeout_raises_on_timeout(monkeypatch, tmp_path):
    script = tmp_path / "slow_fpcalc.py"
    script.write_text("import time\ntime.sleep(5)\n")
    fake_fpcalc = tmp_path / "fpcalc"
    fake_fpcalc.write_text(f"#!/bin/sh\nexec {sys.executable} {script}\n")
    fake_fpcalc.chmod(0o755)
    monkeypatch.setenv("FPCALC", str(fake_fpcalc))

    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"\x00")

    with pytest.raises(acoustid.FingerprintGenerationError):
        acid_mod._fingerprint_file_with_timeout(audio, maxlength=120, timeout=0.3)
