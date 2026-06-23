"""``main._local_tz`` precedence: Docker TZ env (locked, always wins) →
in-app ``settings().timezone`` override → UTC fallback."""
from zoneinfo import ZoneInfo

from dragontag.app.config import store
from dragontag.app.main import _local_tz


def _set_timezone(value: str) -> None:
    store().update({"timezone": value})


def test_local_tz_uses_in_app_setting_when_no_env_tz(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    _set_timezone("America/New_York")
    assert _local_tz() == ZoneInfo("America/New_York")
    _set_timezone("")


def test_local_tz_env_tz_wins_over_in_app_setting(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    _set_timezone("America/New_York")
    assert _local_tz() == ZoneInfo("Europe/Berlin")
    _set_timezone("")


def test_local_tz_defaults_to_utc_with_nothing_set(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    _set_timezone("")
    assert _local_tz() == ZoneInfo("UTC")


def test_local_tz_falls_back_to_utc_on_invalid_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Not/AZone")
    assert _local_tz() == ZoneInfo("UTC")
