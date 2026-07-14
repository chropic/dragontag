"""Artist-folder unification (Fix artist folders): fold-key grouping,
majority-vote election, per-file move under the canonical folder, MB
album-artist-id alias merging, and the case-only directory rename."""
import wave
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.library.actions import (
    _elect_canonical_artist,
    _normalize_album_key,
    _rename_artist_dir,
    unify_artist_folders,
)
from dragontag.app.library.actions import prune_library
from dragontag.app.models import LibraryFolder, Track
from dragontag.app.timeutil import now_utc


class _Ctx:
    """Minimal TaskCtx capturing log lines for report-only assertions."""
    def __init__(self):
        self.lines: list[str] = []

    def log(self, msg):
        self.lines.append(msg)

    def progress(self, *a, **k):
        pass

    def check_cancelled(self):
        pass


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="test")
        s.add(f)
        s.commit()
        s.refresh(f)
        fid = f.id
    yield fid, tmp_path
    with session() as s:
        for t in s.exec(select(Track).where(Track.library_folder_id == fid)).all():
            s.delete(t)
        row = s.get(LibraryFolder, fid)
        if row:
            s.delete(row)
        s.commit()


def _make_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 100)


def _add_track(fid: int, path: Path, **kw) -> int:
    _make_wav(path)
    with session() as s:
        t = Track(library_folder_id=fid, path=str(path), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


# --- election --------------------------------------------------------------


def test_election_majority_lowercase_beats_capitalized_minority():
    now = now_utc()
    tracks = [
        Track(path="a", album_artist="fakemink", indexed_at=now),
        Track(path="b", album_artist="fakemink", indexed_at=now),
        Track(path="c", album_artist="Fakemink", indexed_at=now),
    ]
    # pure majority vote — stylized lowercase wins, no "prefer capitals" bias
    assert _elect_canonical_artist(tracks) == "fakemink"


def test_election_tie_breaks_by_newest_indexed_at():
    now = now_utc()
    tracks = [
        Track(path="a", album_artist="LUCKI", indexed_at=now - timedelta(days=1)),
        Track(path="b", album_artist="Lucki", indexed_at=now),
    ]
    assert _elect_canonical_artist(tracks) == "Lucki"


# --- end-to-end move -------------------------------------------------------


def test_case_variant_folders_merge_into_one_albums_preserved(folder):
    fid, root = folder
    _add_track(fid, root / "fakemink" / "AlbumA" / "01.wav",
               album="AlbumA", album_artist="fakemink")
    _add_track(fid, root / "fakemink" / "AlbumA" / "02.wav",
               album="AlbumA", album_artist="fakemink")
    minority = _add_track(fid, root / "Fakemink" / "AlbumB" / "03.wav",
                          album="AlbumB", album_artist="Fakemink")

    out = unify_artist_folders(fid)

    assert out["groups"] == 1
    assert out["tracks_fixed"] == 1
    with session() as s:
        moved = s.get(Track, minority)
        # album_artist unified, album left untouched
        assert moved.album_artist == "fakemink"
        assert moved.album == "AlbumB"
        p = Path(moved.path)
        assert p.exists()
        assert p.parent.name == "AlbumB"
        assert p.parent.parent.name == "fakemink"
    # loser artist dir pruned once emptied
    assert not (root / "Fakemink").exists()


def test_protected_track_untouched(folder):
    fid, root = folder
    _add_track(fid, root / "glaive" / "A" / "01.wav", album="A", album_artist="glaive")
    _add_track(fid, root / "glaive" / "A" / "02.wav", album="A", album_artist="glaive")
    prot = _add_track(fid, root / "Glaive" / "B" / "03.wav",
                      album="B", album_artist="Glaive", protected=True)

    unify_artist_folders(fid)

    with session() as s:
        p = s.get(Track, prot)
        assert p.album_artist == "Glaive"
        assert Path(p.path).parent.parent.name == "Glaive"
        assert Path(p.path).exists()


def test_mb_album_artist_id_merges_alias_variants(folder):
    fid, root = folder
    # Two spellings that fold DIFFERENTLY but share the MB album-artist id.
    _add_track(fid, root / "A$AP Ferg" / "Alb" / "01.wav",
               album="Alb", album_artist="A$AP Ferg", mb_album_artist_id="artist-ferg")
    _add_track(fid, root / "A$AP Ferg" / "Alb" / "02.wav",
               album="Alb", album_artist="A$AP Ferg", mb_album_artist_id="artist-ferg")
    ferg = _add_track(fid, root / "FERG" / "Solo" / "03.wav",
                      album="Solo", album_artist="FERG", mb_album_artist_id="artist-ferg")

    out = unify_artist_folders(fid)

    assert out["groups"] == 1
    with session() as s:
        moved = s.get(Track, ferg)
        assert moved.album_artist == "A$AP Ferg"
        assert Path(moved.path).parent.parent.name == "A$AP Ferg"


def test_idempotent_second_run_is_noop(folder):
    fid, root = folder
    _add_track(fid, root / "bones" / "A" / "01.wav", album="A", album_artist="bones")
    _add_track(fid, root / "bones" / "A" / "02.wav", album="A", album_artist="bones")
    _add_track(fid, root / "BONES" / "B" / "03.wav", album="B", album_artist="BONES")

    unify_artist_folders(fid)
    out2 = unify_artist_folders(fid)
    assert out2["tracks_fixed"] == 0


# --- directory rename helper ----------------------------------------------


def test_rename_artist_dir_plain_and_updates_paths(folder):
    fid, root = folder
    tid = _add_track(fid, root / "lowercase" / "Alb" / "01.wav",
                     album="Alb", album_artist="lowercase")
    src = root / "lowercase"
    dst = root / "Lowercase"

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
        ok = _rename_artist_dir(s, tracks, src, dst)

    assert ok
    assert dst.exists() and not src.exists()
    with session() as s:
        t = s.get(Track, tid)
        assert Path(t.path).parent.parent.name == "Lowercase"
        assert Path(t.path).exists()


def test_rename_artist_dir_refuses_distinct_existing_target(folder):
    fid, root = folder
    _add_track(fid, root / "src" / "A" / "01.wav", album="A", album_artist="src")
    _add_track(fid, root / "dst" / "B" / "02.wav", album="B", album_artist="dst")

    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
        ok = _rename_artist_dir(s, tracks, root / "src", root / "dst")

    assert ok is False
    assert (root / "src").exists() and (root / "dst").exists()


# --- Phase B: offline album key folds variants ----------------------------


def test_album_key_folds_case_and_edition_and_single_suffix():
    # X / X (Deluxe) / X - Single all fold to the same key
    base = _normalize_album_key("Spiderr", "Bladee")
    assert _normalize_album_key("SPIDERR", "Bladee") == base
    assert _normalize_album_key("Spiderr (Deluxe)", "Bladee") == base
    assert _normalize_album_key("Spiderr - Single", "Bladee") == base
    assert _normalize_album_key("Spiderr - EP", "Bladee") == base
    # trailing dangling dash from sanitization
    assert _normalize_album_key("Spiderr–", "Bladee") == base


def test_album_key_does_not_overstrip_single_ladies():
    # "Single Ladies" must not lose "Single" (only a trailing " - Single" does)
    key = _normalize_album_key("Single Ladies", "Beyonce")
    assert key == ("single ladies", "beyonce")


# --- Phase C: dead-folder report (report-only) -----------------------------


def test_prune_reports_dead_and_orphan_disc_folders(folder):
    fid, root = folder
    # A real album with audio (must NOT be reported).
    _add_track(fid, root / "Artist" / "Real Album" / "01.wav",
               album="Real Album", album_artist="Artist")
    # Dead folder: cover art, no audio anywhere below.
    (root / "Artist" / "Ghost Album").mkdir(parents=True)
    (root / "Artist" / "Ghost Album" / "cover.jpg").write_bytes(b"\xff\xd8")
    # Orphan disc: album folder with only Disc 02, disc 1 absent.
    (root / "Mac Miller" / "GO_OD AM" / "Disc 02").mkdir(parents=True)
    _add_track(fid, root / "Mac Miller" / "GO_OD AM" / "Disc 02" / "01.wav",
               album="GO_OD AM", album_artist="Mac Miller", disc_num=2)

    ctx = _Ctx()
    out = prune_library(fid, ctx=ctx)

    assert out["dead_reported"] >= 2
    joined = "\n".join(ctx.lines)
    assert "dead folder" in joined
    assert "orphan disc" in joined
    # the real album must never be flagged
    assert "Real Album" not in joined


# --- route -----------------------------------------------------------------


def test_route_requires_auth_and_queues_job(folder):
    from fastapi.testclient import TestClient

    from dragontag.app.main import app, require_auth

    fid, _root = folder

    # Without auth: redirect to login / 401, never queues.
    unauth = TestClient(app, follow_redirects=False)
    resp = unauth.post("/library/unify-artist-folders", data={"folder_id": fid})
    assert resp.status_code in (401, 303, 307)

    # With auth: a background job is queued (303 toast redirect).
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/library/unify-artist-folders", data={"folder_id": fid})
        assert resp.status_code == 303
    finally:
        app.dependency_overrides.pop(require_auth, None)
