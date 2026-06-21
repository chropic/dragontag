"""S8: network timeouts must follow live settings changes, not latch onto
whatever value was in effect the first time a network call was made.
"""
import dragontag.app.identify.musicbrainz as mbq
from dragontag.app.config import store


def test_ensure_configured_reapplies_timeout_on_every_call(monkeypatch):
    seen = []
    monkeypatch.setattr(mbq.socket, "setdefaulttimeout", lambda t: seen.append(t))

    store().update({"network_timeout_seconds": 5.0})
    mbq._ensure_configured()
    store().update({"network_timeout_seconds": 42.0})
    mbq._ensure_configured()

    assert seen == [5.0, 42.0]


def test_lyrics_fetch_uses_configured_network_timeout(monkeypatch):
    from dragontag.app.tagging import lyrics_fetcher

    store().update({"network_timeout_seconds": 7.0})
    seen_timeouts = []

    def fake_fetch_bytes(url, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        return type("R", (), {"status_code": 404})(), b""

    monkeypatch.setattr("dragontag.app.net.fetch_bytes", fake_fetch_bytes)
    lyrics_fetcher.fetch("Artist", "Title")

    assert seen_timeouts and all(t == 7.0 for t in seen_timeouts)
