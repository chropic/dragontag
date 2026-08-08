"""Compilation candidates are suppressed unless the source names that album."""
from pathlib import Path
from types import SimpleNamespace

from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.identify import relookup
from dragontag.app.ingest import album as album_mod
from dragontag.app.models import Job


def _release(
    rid: str,
    title: str,
    *,
    status: str = "Official",
    compilation: bool = True,
    various_artists: bool = False,
) -> dict:
    group = {"id": f"rg-{rid}", "primary-type": "Album"}
    if compilation:
        group["secondary-type-list"] = ["Compilation"]
    release = {
        "id": rid,
        "title": title,
        "status": status,
        "release-group": group,
        "medium-list": [{
            "track-list": [{
                "position": 1,
                "title": "Caroline",
                "recording": {"id": f"rec-{rid}", "title": "Caroline"},
            }],
        }],
    }
    if various_artists:
        release["artist-credit"] = [{
            "artist": {
                "id": "89ad4ac3-39f7-470e-963a-56509c546377",
                "name": "Various Artists",
            },
        }]
    return release


def _candidate(release: dict) -> mbq.Candidate:
    return mbq.Candidate(
        score=0.99,
        recording_id=f"rec-{release['id']}",
        release_id=release["id"],
        raw_recording={"title": "Caroline", "artist-credit-phrase": "Aminé"},
        raw_release=release,
    )


def test_context_aware_compilation_policy():
    official = _release("hits", "2010s Hits")
    bootleg = _release("bootleg", "Gym Hip-Hop", status="Bootleg")
    ordinary = _release("album", "Good for You", compilation=False)
    va = _release("va", "Best of 2010", compilation=False, various_artists=True)

    assert not mbq.matchmaking_release_allowed(official, album_hints="Good for You")
    assert mbq.matchmaking_release_allowed(official, album_hints="2010s HITS")
    assert not mbq.matchmaking_release_allowed(bootleg, album_hints="Gym Hip-Hop")
    assert mbq.matchmaking_release_allowed(ordinary, album_hints=None)
    assert not mbq.matchmaking_release_allowed(va, album_hints="Good for You")


def test_text_search_filters_compilation_noise(monkeypatch):
    noisy = _release("hits", "2010s Hits")
    useful = _release("album", "Good for You", compilation=False)
    response = {
        "recording-list": [{
            "id": "rec-1",
            "title": "Caroline",
            "artist-credit-phrase": "Aminé",
            "ext:score": "100",
            "release-list": [noisy, useful],
        }],
    }
    monkeypatch.setattr(mbq, "_ensure_configured", lambda: None)
    monkeypatch.setattr(mbq.mb, "search_recordings", lambda **kwargs: response)
    monkeypatch.setattr(mbq, "_mb_retry", lambda fn, **kwargs: fn(**kwargs))

    found = mbq.search_candidates(
        title="Caroline", artist="Aminé", album="Good for You"
    )
    assert [candidate.release_id for candidate in found] == ["album"]


def test_direct_mbid_is_explicit_override(monkeypatch):
    explicit = _release("explicit", "Gym Hip-Hop", status="Bootleg")
    recording = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Caroline",
        "release-list": [explicit],
    }
    monkeypatch.setattr(mbq, "_ensure_configured", lambda: None)
    monkeypatch.setattr(mbq, "fetch_recording", lambda _rid: recording)

    found = mbq.candidates_from_mbid(recording["id"])
    assert [candidate.release_id for candidate in found] == ["explicit"]


def test_acoustid_expansion_is_filtered(monkeypatch):
    noisy = _candidate(_release("hits", "2010s Hits"))
    monkeypatch.setattr(
        relookup.acoustid,
        "lookup",
        lambda _path: [SimpleNamespace(recording_id="rec-hits")],
    )
    monkeypatch.setattr(mbq, "candidates_from_mbid", lambda _rid: [noisy])
    monkeypatch.setattr(mbq, "fetch_release", lambda _rid: noisy.raw_release)

    found, fingerprinted = relookup.candidates_for_file(
        Path("song.flac"), album="Good for You", text_fallback=False
    )
    assert found == []
    assert fingerprinted is False


def test_album_election_filters_compilation_candidates(monkeypatch):
    bad = _release("hits", "2010s Hits")
    good = _release("album", "Good for You", compilation=False)
    job = Job(id=991, source_path="song.wav", original_name="song.wav")
    monkeypatch.setattr(album_mod, "_load_group_jobs", lambda _key: [job])
    monkeypatch.setattr(
        album_mod,
        "_job_clues",
        lambda _job: {"title": "Caroline", "artist": "Aminé", "album": "Good for You"},
    )
    monkeypatch.setattr(album_mod, "_candidate_release_ids", lambda _clues: ["hits", "album"])
    monkeypatch.setattr(mbq, "fetch_release", lambda rid: {"hits": bad, "album": good}[rid])
    monkeypatch.setattr(album_mod, "_match_file_to_release", lambda clues, rel: (1.0, f"rec-{rel['id']}"))
    monkeypatch.setattr(album_mod, "_library_majority_release", lambda _rel: False)

    elected = album_mod.elect_release("group")
    assert elected is not None
    assert elected.release_id == "album"
