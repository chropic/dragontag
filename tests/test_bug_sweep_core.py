"""Regression tests for the core/infra bug sweep (notify, models, logsetup,
schema totals, musicbrainz credit phrase, scheduler cron timezone)."""
import time
from datetime import datetime, timezone

from dragontag.app import logsetup, notify
from dragontag.app.identify.musicbrainz import _credit_phrase
from dragontag.app.models import MAX_JOB_LOG_BYTES, append_job_log
from dragontag.app.tagging.schema import TrackTags


# ---- notify: fire-and-forget contract must hold in the caller thread ----

class _BrokenTags:
    # No title/artist_display/album attributes at all.
    pass


class _Job:
    id = 1
    original_name = "x.mp3"
    error = None


def test_post_done_with_broken_tags_does_not_raise(monkeypatch):
    from dragontag.app.config import settings
    s = settings()
    monkeypatch.setattr(s, "webhook_url", "http://localhost:9/nope", raising=False)
    monkeypatch.setattr(s, "webhook_on_done", True, raising=False)
    monkeypatch.setattr("dragontag.app.config.settings", lambda: s)
    notify.post_done(_Job(), _BrokenTags())  # must not raise


def test_post_error_settings_failure_does_not_raise(monkeypatch):
    def _boom():
        raise RuntimeError("settings store corrupted")
    monkeypatch.setattr("dragontag.app.config.settings", _boom)
    notify.post_error(_Job())  # must not raise


# ---- append_job_log: cap is bytes, not characters ----

def test_append_job_log_caps_multibyte_input_in_bytes():
    log = append_job_log(None, "驪" * MAX_JOB_LOG_BYTES)  # 3 bytes each in UTF-8
    assert len(log.encode("utf-8")) <= MAX_JOB_LOG_BYTES
    assert log.startswith("…[earlier log truncated]…\n")


def test_append_job_log_no_truncation_under_cap():
    assert append_job_log("a", "b") == "ab"


# ---- logsetup: corrupted verbosity setting must not raise ----

def test_logsetup_non_numeric_verbosity_defaults_to_info():
    logsetup.apply("garbage")
    logsetup.apply(None)


# ---- schema: totals of 0 mean unknown, no literal "0" written ----

def test_zero_totals_not_written():
    d = TrackTags(title="t", track=3, track_total=0, disc=1, disc_total=0).to_vorbis("; ")
    assert "TRACKTOTAL" not in d
    assert "TOTALTRACKS" not in d
    assert "DISCTOTAL" not in d
    assert "TOTALDISCS" not in d
    assert d["track"] == "03"


def test_real_totals_still_written():
    d = TrackTags(title="t", track=3, track_total=12).to_vorbis("; ")
    assert d["TRACKTOTAL"] == "12"
    assert d["track"] == "03/12"


# ---- musicbrainz: artist-credit with explicit null artist ----

def test_credit_phrase_with_null_artist_entry():
    credits = [
        {"name": None, "artist": None, "joinphrase": " feat. "},
        {"artist": {"name": "Bladee"}},
    ]
    assert _credit_phrase(credits) == "feat. Bladee"


# ---- scheduler: cron interpreted in local time, returned as naive UTC ----

def test_next_run_interprets_cron_in_local_time(monkeypatch):
    import importlib
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        from dragontag.app import scheduler
        base = datetime(2026, 1, 15, 12, 0, 0)  # naive UTC = 04:00 local
        nxt = scheduler.next_run("0 6 * * *", base)
        # 6 AM local on Jan 15 (PST, UTC-8) is 14:00 UTC.
        assert nxt == datetime(2026, 1, 15, 14, 0, 0)
        assert nxt.tzinfo is None
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_next_run_invalid_expression_returns_none():
    from dragontag.app import scheduler
    assert scheduler.next_run("not a cron") is None
