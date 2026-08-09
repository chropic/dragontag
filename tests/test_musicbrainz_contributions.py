import json
import types

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from dragontag.app.contribute import musicbrainz as mbc
from dragontag.app.db import session
from dragontag.app.main import app, require_auth
from dragontag.app.models import Job, JobStatus, MusicBrainzContribution, ReviewReason
from dragontag.app.tagging.schema import TrackTags


@pytest.fixture()
def client():
    app.dependency_overrides[require_auth] = lambda: None
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)


def job_id(reason=ReviewReason.no_match):
    with session() as s:
        job = Job(
            source_path="/tmp/contribute.flac",
            original_name="contribute.flac",
            status=JobStatus.needs_review,
            review_reason=reason,
            chosen_tags_json={"title": "Track", "artists": ["Artist"], "album": "Release"},
        )
        s.add(job); s.commit(); s.refresh(job)
        return job.id


def release_draft():
    return {
        "release": {
            "title": "Release",
            "artists": [{"name": "Artist"}],
            "release_group": {"title": "Release"},
            "date": "2026-08",
            "country": "US",
            "type": "Album",
            "status": "Official",
            "labels": [{"name": "Label", "catalog_number": "CAT-1"}],
            "barcode": "",
        },
        "media": [
            {"position": 1, "format": "CD", "tracks": [
                {"position": 1, "title": "Track", "artists": [{"name": "Artist"}], "duration_ms": 123000}
            ]},
            {"position": 2, "format": "Digital Media", "tracks": [
                {"position": 1, "title": "Bonus", "artists": [{"name": "Guest"}]}
            ]},
        ],
        "provenance": "Artist site and physical packaging",
        "edit_note": "Transcribed from the cited sources.",
    }


def test_release_and_standalone_validation_and_seed_builders():
    snapshot = mbc.validate_release_draft(release_draft())
    payload = mbc.build_release_seed_payload(snapshot, {}, "http://local/return")
    assert payload["name"] == ["Release"]
    assert payload["mediums.1.track.0.name"] == ["Bonus"]
    assert payload["redirect_uri"] == ["http://local/return"]
    standalone = mbc.validate_standalone_draft({
        "title": "Track", "artists": [{"name": "Artist"}], "duration_ms": 123000,
        "isrc": "USAAA2600001", "source": "Artist site", "edit_note": "Verified source",
    })
    standalone_payload = mbc.build_standalone_seed_payload(standalone, {})
    assert standalone_payload["edit-recording.name"] == ["Track"]


def test_contribution_page_is_reason_gated_and_renders_both_modes(client):
    jid = job_id()
    response = client.get(f"/review/{jid}/contribute", params={"mode": "release"})
    assert response.status_code == 200
    assert "Release drafts accept any number of media and tracks" in response.text
    assert "standalone recording" in response.text
    wrong_reason = job_id(ReviewReason.low_score)
    assert client.get(f"/review/{wrong_reason}/contribute").status_code == 409


def _bad_country(draft):
    draft["release"]["country"] = "USA"


def _missing_track_title(draft):
    draft["media"][0]["tracks"][0]["title"] = ""


def _bad_track_total(draft):
    draft["media"][0]["track_total"] = 99


@pytest.mark.parametrize("change", [_bad_country, _missing_track_title, _bad_track_total])
def test_release_validation_rejects_invalid_required_data(change):
    draft = release_draft()
    change(draft)
    with pytest.raises(mbc.ContributionValidationError):
        mbc.validate_release_draft(draft)


def test_handoff_requires_explicit_duplicate_decisions(client):
    jid = job_id()
    results = {"artists": {"Artist": [{
        "id": "11111111-1111-1111-1111-111111111111", "name": "Artist",
        "score": 1.0, "plausible": True, "disambiguation": "",
    }]}}
    with session() as s:
        row = MusicBrainzContribution(
            job_id=jid, mode="release", status="draft",
            draft_snapshot_json=mbc.validate_release_draft(release_draft()),
            duplicate_results_json=results,
        )
        s.add(row); s.commit(); s.refresh(row); cid = row.id
    missing = client.post(f"/review/{jid}/contribution/{cid}/handoff", data={"decisions_json": "{}"})
    assert missing.status_code == 303
    key = mbc.plausible_decision_keys(results)[0]
    response = client.post(
        f"/review/{jid}/contribution/{cid}/handoff",
        data={"decisions_json": json.dumps({key: "reuse"})},
    )
    assert response.status_code == 200
    assert "Official MusicBrainz editor" in response.text
    with session() as s:
        assert s.get(MusicBrainzContribution, cid).status == "handoff"


def test_return_get_is_non_mutating_and_result_post_is_pending(client):
    jid = job_id()
    with session() as s:
        row = MusicBrainzContribution(job_id=jid, mode="release", status="handoff")
        s.add(row); s.commit(); s.refresh(row); cid = row.id
    release_id = "22222222-2222-2222-2222-222222222222"
    response = client.get("/musicbrainz/return", params={
        "job_id": jid, "contribution_id": cid, "release_mbid": release_id,
    })
    assert response.status_code == 200
    with session() as s:
        assert s.get(MusicBrainzContribution, cid).status == "handoff"
    response = client.post(
        f"/review/{jid}/contribution/{cid}/result", data={"release_mbid": release_id}
    )
    assert response.status_code == 303
    with session() as s:
        row = s.get(MusicBrainzContribution, cid)
        assert row.status == "submitted"
        assert row.returned_mbids_json["release"] == release_id


def test_reusing_existing_release_skips_creation_handoff(client):
    jid = job_id()
    release_id = "66666666-6666-6666-6666-666666666666"
    results = {"releases": {"Release": [{
        "id": release_id, "name": "Release", "score": 1.0,
        "plausible": True, "disambiguation": "",
    }]}}
    with session() as s:
        row = MusicBrainzContribution(
            job_id=jid, mode="release", status="draft",
            draft_snapshot_json=mbc.validate_release_draft(release_draft()),
            duplicate_results_json=results,
        )
        s.add(row); s.commit(); s.refresh(row); cid = row.id
    key = mbc.plausible_decision_keys(results)[0]
    response = client.post(
        f"/review/{jid}/contribution/{cid}/handoff",
        data={"decisions_json": json.dumps({key: "reuse"})},
    )
    assert response.status_code == 303
    with session() as s:
        row = s.get(MusicBrainzContribution, cid)
        assert row.status == "submitted"
        assert row.returned_mbids_json["release"] == release_id
        assert row.seed_payload_json == {}


def test_preflight_is_tracked_and_network_free_when_mocked(client, monkeypatch):
    jid = job_id()
    captured = {}
    monkeypatch.setattr(mbc, "search_duplicates", lambda snapshot, mode, ctx=None: {"artists": {}})
    def run_task(kind, name, fn):
        captured.update(kind=kind, fn=fn)
        return 4242
    monkeypatch.setattr("dragontag.app.main.tasks.run_task", run_task)
    response = client.post(f"/review/{jid}/contribution/preflight", data={
        "mode": "release", "draft_json": json.dumps(release_draft()),
    })
    assert response.status_code == 303
    assert captured["kind"] == "mb_preflight"
    ctx = types.SimpleNamespace(
        check_cancelled=lambda: None, progress=lambda *args, **kwargs: None
    )
    captured["fn"](ctx)
    with session() as s:
        rows = s.exec(select(MusicBrainzContribution).where(
            MusicBrainzContribution.job_id == jid
        )).all()
        assert rows[-1].status == "draft"
        assert rows[-1].task_id == 4242


def test_refresh_preview_then_confirmed_apply(client, monkeypatch):
    jid = job_id()
    release_id = "33333333-3333-3333-3333-333333333333"
    recording_id = "44444444-4444-4444-4444-444444444444"
    with session() as s:
        row = MusicBrainzContribution(
            job_id=jid,
            mode="release",
            status="submitted",
            draft_snapshot_json=mbc.validate_release_draft(release_draft()),
            returned_mbids_json={"release": release_id, "recording": recording_id},
        )
        s.add(row); s.commit(); s.refresh(row); cid = row.id
    queued = []
    monkeypatch.setattr(
        "dragontag.app.main.tasks.run_task",
        lambda kind, name, fn: (queued.append(fn) or (5000 + len(queued))),
    )
    tags = TrackTags(
        title="Verified Track", artist_display="Verified Artist", artists=["Verified Artist"],
        album="Verified Release", album_artist_display="Verified Artist",
        album_artists=["Verified Artist"], release_type="Album",
        mb_track_id=recording_id, mb_album_id=release_id,
    )
    monkeypatch.setattr(mbc, "refresh_result", lambda mode, snapshot, mbids: {
        "state": "verified", "recording_id": recording_id, "release_id": release_id,
        "title": tags.title, "artist": tags.artist_display, "album": tags.album,
        "tags": {key: value for key, value in tags.__dict__.items() if key != "cover_bytes"},
    })
    response = client.post(f"/review/{jid}/contribution/{cid}/refresh")
    assert response.status_code == 303
    ctx = types.SimpleNamespace(check_cancelled=lambda: None, log=lambda *args: None)
    queued.pop(0)(ctx)
    with session() as s:
        assert s.get(MusicBrainzContribution, cid).status == "verified"

    def fake_commit(s, job, src, tags, *, score):
        job.status = JobStatus.done
        s.add(job); s.commit()
    monkeypatch.setattr("dragontag.app.ingest.pipeline._commit_tag_path", fake_commit)
    response = client.post(f"/review/{jid}/contribution/{cid}/apply")
    assert response.status_code == 303
    queued.pop(0)(ctx)
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.done
        row = s.get(MusicBrainzContribution, cid)
        assert row.status == "applied"
        assert row.applied_at is not None


def test_failed_refresh_never_resolves_or_mutates_job(client, monkeypatch):
    jid = job_id()
    with session() as s:
        row = MusicBrainzContribution(
            job_id=jid, mode="standalone", status="submitted",
            draft_snapshot_json={"title": "Track"},
            returned_mbids_json={"recording": "55555555-5555-5555-5555-555555555555"},
        )
        s.add(row); s.commit(); s.refresh(row); cid = row.id
    queued = []
    monkeypatch.setattr(
        "dragontag.app.main.tasks.run_task",
        lambda kind, name, fn: (queued.append(fn) or 6000),
    )
    monkeypatch.setattr(mbc, "refresh_result", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    assert client.post(f"/review/{jid}/contribution/{cid}/refresh").status_code == 303
    ctx = types.SimpleNamespace(check_cancelled=lambda: None, log=lambda *args: None)
    with pytest.raises(RuntimeError, match="offline"):
        queued[0](ctx)
    with session() as s:
        assert s.get(Job, jid).status == JobStatus.needs_review
        assert s.get(MusicBrainzContribution, cid).status == "refresh_error"
