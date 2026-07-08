"""dragontag stores advisory as 0=clean/1=explicit; the MP4 ``rtng`` atom uses
iTunes' scale where 2=clean (0 means "no advisory" and hides the Clean badge).
"""
from dragontag.app.tagging.partial import advisory_to_rtng, write_advisory


class _FakeMP4:
    store: dict = {}

    def __init__(self, _path):
        self.tags = _FakeMP4.store

    def save(self):
        pass


def test_mapping():
    assert advisory_to_rtng(1) == 1   # explicit
    assert advisory_to_rtng(0) == 2   # clean — NOT 0


def test_write_advisory_mp4_uses_itunes_scale(tmp_path, monkeypatch):
    p = tmp_path / "t.m4a"
    p.write_bytes(b"\x00")
    _FakeMP4.store = {}
    monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)

    write_advisory(p, 0)
    assert _FakeMP4.store["rtng"] == [2]

    write_advisory(p, 1)
    assert _FakeMP4.store["rtng"] == [1]
