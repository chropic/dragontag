"""Album-first identification: jobs sharing a group_key are tagged against ONE
elected MusicBrainz release, so release-level tags can't scatter across
editions (the album-splitting bug: one folder ending up with several
MUSICBRAINZ_ALBUMIDs / DATEs / RELEASESTATUSes and showing as multiple albums
in players)."""
import wave
from pathlib import Path

import pytest

from dragontag.app.db import session
from dragontag.app.identify import existing_tags
from dragontag.app.identify import musicbrainz as mbq
from dragontag.app.ingest import album as album_mod
from dragontag.app.ingest import pipeline
from dragontag.app.models import Job, JobStatus, ReviewReason
from dragontag.app.tagging.schema import TrackTags


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def _release_doc(rid: str, title: str, tracks: list[tuple[str, str, int]], *,
                 status: str = "Official") -> dict:
    """tracks: [(recording_id, title, position)] — 200s recordings."""
    return {
        "id": rid,
        "title": title,
        "status": status,
        "release-group": {"id": f"rg-{rid}"},
        "medium-list": [
            {
                "position": 1,
                "format": "Digital Media",
                "track-count": len(tracks),
                "track-list": [
                    {
                        "id": f"trk-{rec_id}",
                        "position": pos,
                        "title": t,
                        "recording": {"id": rec_id, "title": t, "length": 200000},
                        "length": 200000,
                    }
                    for rec_id, t, pos in tracks
                ],
            }
        ],
    }


REL_GOOD = _release_doc(
    "rel-good", "Terrified",
    [("r1", "Alpha Song", 1), ("r2", "Beta Song", 2), ("r3", "Gamma Song", 3)],
)
# A drifted edition one file's old tags point at: covers only one recording.
REL_ALT = _release_doc("rel-alt", "Terrified (Deluxe)", [("r2x", "Beta Song", 1)])

_RELEASES = {"rel-good": REL_GOOD, "rel-alt": REL_ALT}


@pytest.fixture(autouse=True)
def _fresh_memo():
    album_mod._elections.clear()
    yield
    album_mod._elections.clear()


@pytest.fixture()
def _stubbed_mb(monkeypatch):
    """Stub every network touchpoint of the group + commit paths."""
    import uuid

    token = uuid.uuid4().hex[:8]  # unique canonical filenames per test — the
    # test library dir is shared across the whole session.
    tags_by_file: dict[str, dict] = {}

    def read_stub(path: Path) -> dict:
        return dict(tags_by_file.get(Path(path).name, {}))

    monkeypatch.setattr(existing_tags, "read", read_stub)

    def search_stub(*, title, artist, album, duration_sec=None, limit=10, **kw):
        out = []
        for rid, rel in _RELEASES.items():
            for medium in rel["medium-list"]:
                for trk in medium["track-list"]:
                    if title and trk["title"].lower() == title.lower():
                        out.append(mbq.Candidate(
                            score=0.95,
                            recording_id=trk["recording"]["id"],
                            release_id=rid,
                            raw_recording=trk["recording"],
                            raw_release={"id": rid, "title": rel["title"]},
                        ))
        return out

    monkeypatch.setattr(mbq, "search_candidates", search_stub)
    monkeypatch.setattr(mbq, "fetch_release", lambda rid: _RELEASES[rid])

    def assemble_stub(*, release_id, recording_id, rel=None):
        rel = rel or _RELEASES[release_id]
        pos = next(
            (int(t["position"]) for m in rel["medium-list"] for t in m["track-list"]
             if t["recording"]["id"] == recording_id),
            1,
        )
        return TrackTags(
            title=f"title-{recording_id}-{token}",
            artists=["Artist"], artist_display="Artist",
            album=rel["title"], album_artist_display="Artist",
            mb_track_id=recording_id, mb_album_id=release_id,
            mb_release_group_id=rel["release-group"]["id"],
            track=pos, track_total=3, release_status=rel.get("status"),
        )

    monkeypatch.setattr(mbq, "assemble_tags", assemble_stub)
    monkeypatch.setattr(pipeline, "fetch_for_release", lambda _rid: None)
    from dragontag.app.tagging import lyrics_fetcher
    monkeypatch.setattr(lyrics_fetcher, "fetch", lambda **kw: None)
    return tags_by_file


def _enqueue_group(folder: Path, names: list[str], *, dry_run: bool | None = None):
    jobs = []
    for n in names:
        p = folder / n
        _make_wav(p)
        jobs.append(pipeline.enqueue(p, dry_run=dry_run, group_key=str(folder.resolve())))
    return jobs


def test_group_converges_on_one_release(tmp_path, _stubbed_mb):
    _stubbed_mb.update({
        "01.wav": {"title": "Alpha Song", "artist": "Artist", "track": "1", "duration": 200.0},
        # Drifted pre-existing MBIDs: per-track path would short-circuit onto
        # rel-alt; the group path must demote them and converge on rel-good.
        "02.wav": {"title": "Beta Song", "artist": "Artist", "track": "2", "duration": 200.0,
                   "mb_track_id": "r2x", "mb_album_id": "rel-alt"},
        "03.wav": {"title": "Gamma Song", "artist": "Artist", "track": "3", "duration": 200.0},
    })
    folder = tmp_path / "drop" / "Terrified"
    jobs = _enqueue_group(folder, ["01.wav", "02.wav", "03.wav"])
    for j in jobs:
        pipeline.process(j.id)

    with session() as s:
        album_ids = set()
        for j in jobs:
            row = s.get(Job, j.id)
            assert row.status == JobStatus.done, row.log
            album_ids.add(row.chosen_tags_json["mb_album_id"])
            assert row.chosen_tags_json["mb_release_group_id"] == "rg-rel-good"
            assert row.chosen_tags_json["release_status"] == "Official"
        assert album_ids == {"rel-good"}


def test_stray_file_goes_to_album_mismatch_review(tmp_path, _stubbed_mb):
    _stubbed_mb.update({
        "01.wav": {"title": "Alpha Song", "artist": "Artist", "track": "1", "duration": 200.0},
        "02.wav": {"title": "Beta Song", "artist": "Artist", "track": "2", "duration": 200.0},
        "03.wav": {"title": "Gamma Song", "artist": "Artist", "track": "3", "duration": 200.0},
        "99.wav": {"title": "Totally Unrelated Banger", "artist": "Nobody", "duration": 45.0},
    })
    folder = tmp_path / "drop" / "TerrifiedPlus"
    jobs = _enqueue_group(folder, ["01.wav", "02.wav", "03.wav", "99.wav"])
    for j in jobs:
        pipeline.process(j.id)

    with session() as s:
        matched = [s.get(Job, j.id) for j in jobs[:3]]
        stray = s.get(Job, jobs[3].id)
        for row in matched:
            assert row.status == JobStatus.done, row.log
            assert row.chosen_tags_json["mb_album_id"] == "rel-good"
        assert stray.status == JobStatus.needs_review
        assert stray.review_reason == ReviewReason.album_mismatch


def test_low_score_group_routes_all_to_review(tmp_path, _stubbed_mb, monkeypatch):
    _stubbed_mb.update({
        # Weak matches: titles found by search but sabotage the per-track
        # match with wrong durations/track numbers.
        "01.wav": {"title": "Alpha Song", "artist": "Artist", "track": "9", "duration": 80.0},
        "02.wav": {"title": "Beta Song", "artist": "Artist", "track": "8", "duration": 80.0},
    })
    folder = tmp_path / "drop" / "Weak"
    jobs = _enqueue_group(folder, ["01.wav", "02.wav"])
    for j in jobs:
        pipeline.process(j.id)

    with session() as s:
        for j in jobs:
            row = s.get(Job, j.id)
            assert row.status == JobStatus.needs_review, row.log
            assert row.review_reason == ReviewReason.low_score
            # Elected candidate is first, so one review click applies the
            # group consensus.
            first = row.candidates_json["items"][0]
            assert first["release_id"] == "rel-good"


def test_dry_run_group_stays_preview(tmp_path, _stubbed_mb):
    _stubbed_mb.update({
        "01.wav": {"title": "Alpha Song", "artist": "Artist", "track": "1", "duration": 200.0},
        "02.wav": {"title": "Beta Song", "artist": "Artist", "track": "2", "duration": 200.0},
    })
    folder = tmp_path / "drop" / "Preview"
    jobs = _enqueue_group(folder, ["01.wav", "02.wav"], dry_run=True)
    originals = {j.id: Path(j.source_path).read_bytes() for j in jobs}
    for j in jobs:
        pipeline.process(j.id)

    with session() as s:
        for j in jobs:
            row = s.get(Job, j.id)
            assert row.status == JobStatus.needs_review
            assert row.review_reason == ReviewReason.dry_run
            assert Path(j.source_path).read_bytes() == originals[j.id]


def test_no_election_falls_back_to_per_track(tmp_path, _stubbed_mb, monkeypatch):
    _stubbed_mb.update({
        "01.wav": {"title": "Alpha Song", "artist": "Artist",
                   "mb_track_id": "r1", "mb_album_id": "rel-good"},
        "02.wav": {"title": "Beta Song", "artist": "Artist",
                   "mb_track_id": "r2", "mb_album_id": "rel-good"},
    })
    # MB search dead + no candidates -> election impossible.
    monkeypatch.setattr(mbq, "search_candidates", lambda **kw: [])
    monkeypatch.setattr(
        album_mod, "elect_release", lambda key: None
    )
    folder = tmp_path / "drop" / "Fallback"
    jobs = _enqueue_group(folder, ["01.wav", "02.wav"])
    for j in jobs:
        pipeline.process(j.id)

    with session() as s:
        for j in jobs:
            row = s.get(Job, j.id)
            # Per-track MBID short-circuit still works as the fallback.
            assert row.status == JobStatus.done, row.log
            assert row.chosen_tags_json["mb_album_id"] == "rel-good"


def test_memo_recomputed_when_membership_grows(tmp_path, _stubbed_mb):
    _stubbed_mb.update({
        "01.wav": {"title": "Alpha Song", "artist": "Artist", "track": "1", "duration": 200.0},
        "02.wav": {"title": "Beta Song", "artist": "Artist", "track": "2", "duration": 200.0},
    })
    folder = tmp_path / "drop" / "Growing"
    (j1,) = _enqueue_group(folder, ["01.wav"])
    pipeline.process(j1.id)

    # A later watcher settle adds a sibling: the memoized election must be
    # recomputed to include it, not treat it as an album mismatch.
    (j2,) = _enqueue_group(folder, ["02.wav"])
    pipeline.process(j2.id)

    with session() as s:
        row = s.get(Job, j2.id)
        assert row.status == JobStatus.done, row.log
        assert row.chosen_tags_json["mb_album_id"] == "rel-good"
