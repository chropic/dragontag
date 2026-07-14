"""reidentify_tracks: AcoustID re-identify for untagged tracks, applying only
fingerprint-confirmed matches. Network is fully monkeypatched."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from dragontag.app.db import session
from dragontag.app.identify import relookup
from dragontag.app.library import actions, retag
from dragontag.app.models import LibraryFolder, Track


@pytest.fixture()
def folder(tmp_path):
    with session() as s:
        f = LibraryFolder(path=str(tmp_path), label="t")
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


def _track(fid, path: Path, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    with session() as s:
        s.add(Track(library_folder_id=fid, path=str(path), **kw))
        s.commit()


def _cand(rec="rec1", rel="rel1"):
    return SimpleNamespace(recording_id=rec, release_id=rel, score=0.9)


def test_applies_only_fingerprint_matches(folder, monkeypatch):
    fid, root = folder
    _track(fid, root / "a.flac")                          # untagged -> fingerprint hit
    _track(fid, root / "b.flac")                          # untagged -> text fallback only
    _track(fid, root / "c.flac", mb_track_id="X")         # already identified -> skipped
    _track(fid, root / "d.flac", protected=True)          # protected -> skipped

    def fake_lookup(path, *, title=None, artist=None, album=None, limit=10):
        if path.name == "a.flac":
            return [_cand()], True          # fingerprinted
        return [_cand()], False             # text fallback (not confident)

    applied_calls = []
    monkeypatch.setattr(relookup, "candidates_for_file", fake_lookup)
    monkeypatch.setattr(
        retag, "apply_match",
        lambda tid, rec, rel: (applied_calls.append((tid, rec, rel)) or (True, "ok")),
    )

    out = actions.reidentify_tracks(fid)

    assert out["candidates_checked"] == 2       # only a + b are eligible
    assert out["applied"] == 1                  # only a (fingerprinted) applied
    assert out["no_match"] == 1                 # b was text-only
    assert out["skipped_protected"] == 1
    assert len(applied_calls) == 1 and applied_calls[0][1:] == ("rec1", "rel1")


def test_failed_apply_is_counted(folder, monkeypatch):
    fid, root = folder
    _track(fid, root / "a.flac")
    monkeypatch.setattr(
        relookup, "candidates_for_file",
        lambda path, **kw: ([_cand()], True),
    )
    monkeypatch.setattr(retag, "apply_match", lambda tid, rec, rel: (False, "boom"))

    out = actions.reidentify_tracks(fid)

    assert out["applied"] == 0 and out["failed"] == 1
