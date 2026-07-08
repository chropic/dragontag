"""MP4 snapshot round-trip for non-string atoms.

``pgap``/``pcst`` are bare bools (iterating one raised TypeError, which
``capture()`` swallowed into an *empty* snapshot — revert then silently did
nothing) and int atoms like ``tmpo`` must come back as ints or mutagen raises
on save during restore.
"""
from dragontag.app.tagging.snapshot import _capture_mp4, _restore_mp4


class _FakeMP4:
    store: dict = {}

    def __init__(self, _path):
        self.tags = _FakeMP4.store

    def save(self):
        pass


def _patch_mp4(monkeypatch, initial: dict):
    _FakeMP4.store = dict(initial)
    monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
    return _FakeMP4


def test_capture_handles_bool_and_int_atoms(tmp_path, monkeypatch):
    p = tmp_path / "t.m4a"
    p.write_bytes(b"\x00")
    _patch_mp4(
        monkeypatch,
        {"pgap": True, "pcst": False, "tmpo": [120], "\xa9nam": ["Song"], "cpil": True},
    )

    snap = _capture_mp4(p)

    assert snap["pgap"] == ["1"]
    assert snap["pcst"] == ["0"]
    assert snap["tmpo"] == ["120"]
    assert snap["cpil"] == ["1"]
    assert snap["\xa9nam"] == ["Song"]


def test_restore_coerces_bool_and_int_atoms(tmp_path, monkeypatch):
    p = tmp_path / "t.m4a"
    p.write_bytes(b"\x00")
    fake = _patch_mp4(monkeypatch, {})

    _restore_mp4(
        p,
        {"pgap": ["1"], "tmpo": ["120"], "\xa9nam": ["Song"], "trkn": ["5/12"]},
    )

    assert fake.store["pgap"] is True
    assert fake.store["tmpo"] == [120]
    assert fake.store["\xa9nam"] == ["Song"]
    assert fake.store["trkn"] == [(5, 12)]
