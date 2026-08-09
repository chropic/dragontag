"""``main._local_tz`` precedence: Docker TZ env (locked, always wins) →
in-app ``settings().timezone`` override → UTC fallback."""
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from dragontag.app.config import store
from dragontag.app import main as main_module
from dragontag.app.main import _local_tz, app, require_auth


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


def test_settings_page_reuses_precomputed_timezone_choices(monkeypatch):
    def fail_if_rescanned():
        raise AssertionError("settings request rescanned the host timezone database")

    monkeypatch.setattr(main_module, "available_timezones", fail_if_rescanned)
    app.dependency_overrides[require_auth] = lambda: None
    try:
        response = TestClient(app, follow_redirects=False).get("/settings")
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200
    assert 'value="UTC"' in response.text
