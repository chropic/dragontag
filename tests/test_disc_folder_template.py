"""fix_disc_folders must render the multidisc folder template with the same
placeholder set build_destination supports — a "Disc {disc} of {disctotal}"
template used to KeyError on every rename (silently counted as an error).
"""
import wave
from pathlib import Path

import pytest
from sqlmodel import select

from dragontag.app.config import settings, store
from dragontag.app.library.actions import fix_disc_folders
from dragontag.app.db import session
from dragontag.app.models import LibraryFolder, Track


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


def test_disctotal_placeholder_renders_instead_of_erroring(folder):
    fid, root = folder
    album = root / "Artist" / "Album"
    with session() as s:
        for disc in (1, 2):
            p = album / f"CD{disc}" / "01.wav"
            _make_wav(p)
            t = Track(library_folder_id=fid, path=str(p), title="T",
                      disc_num=disc, disc_total=2)
            s.add(t)
        s.commit()

    old = settings().multidisc_folder_template
    store().update({"multidisc_folder_template": "Disc {disc} of {disctotal}"})
    try:
        out = fix_disc_folders(fid)
    finally:
        store().update({"multidisc_folder_template": old})

    assert out["errors"] == 0
    assert out["renamed"] == 2
    assert (album / "Disc 1 of 2").exists()
    assert (album / "Disc 2 of 2").exists()
    with session() as s:
        rows = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
        assert {Path(r.path).parent.name for r in rows} == {"Disc 1 of 2", "Disc 2 of 2"}
