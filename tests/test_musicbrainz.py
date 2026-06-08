"""Unit tests for candidates_from_mbid (manual MB URL/ID matching)."""
import dragontag.app.identify.musicbrainz as mbq

_UUID = "11111111-1111-1111-1111-111111111111"

_RECORDING = {
    "id": _UUID,
    "title": "Song A",
    "artist-credit-phrase": "Some Artist",
    "release-list": [
        {"id": "rel-aaaa", "title": "Album One"},
        {"id": "rel-bbbb", "title": "Album Two"},
    ],
}

_RELEASE = {
    "id": _UUID,
    "title": "Album One",
    "medium-list": [
        {
            "track-list": [
                {"id": "t1", "title": "Song A", "recording": {"id": "rec-a", "title": "Song A"}},
                {"id": "t2", "title": "Song B", "recording": {"id": "rec-b", "title": "Song B"}},
            ]
        }
    ],
}


def _patch(monkeypatch, *, recording=None, release=None):
    monkeypatch.setattr(mbq, "_ensure_configured", lambda: None)
    monkeypatch.setattr(mbq, "fetch_recording", lambda rid: (recording or {}))
    monkeypatch.setattr(mbq, "fetch_release", lambda lid: (release or {}))


def test_recording_url_lists_releases(monkeypatch):
    _patch(monkeypatch, recording=_RECORDING)
    cands = mbq.candidates_from_mbid(f"https://musicbrainz.org/recording/{_UUID}")
    assert [c.release_id for c in cands] == ["rel-aaaa", "rel-bbbb"]
    assert all(c.recording_id == _UUID for c in cands)


def test_release_url_lists_tracks(monkeypatch):
    _patch(monkeypatch, release=_RELEASE)
    cands = mbq.candidates_from_mbid(f"https://musicbrainz.org/release/{_UUID}")
    assert {c.recording_id for c in cands} == {"rec-a", "rec-b"}
    assert all(c.release_id == _UUID for c in cands)


def test_release_url_filters_by_title_hint(monkeypatch):
    _patch(monkeypatch, release=_RELEASE)
    cands = mbq.candidates_from_mbid(
        f"https://musicbrainz.org/release/{_UUID}", title_hint="Song B"
    )
    assert [c.recording_id for c in cands] == ["rec-b"]


def test_bare_uuid_tries_recording_first(monkeypatch):
    _patch(monkeypatch, recording=_RECORDING, release=_RELEASE)
    cands = mbq.candidates_from_mbid(_UUID)
    # Recording lookup succeeds, so we get its releases.
    assert [c.release_id for c in cands] == ["rel-aaaa", "rel-bbbb"]


def test_junk_input_returns_empty(monkeypatch):
    _patch(monkeypatch, recording=_RECORDING)
    assert mbq.candidates_from_mbid("not an mbid") == []
    assert mbq.candidates_from_mbid("") == []
