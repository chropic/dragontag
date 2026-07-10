"""write_album_link_tags MP4 branch: a total of 0 means "unknown" and must not
destroy the file's existing trkn/disk total half (same truthiness convention
as the FLAC/ID3 branches — the recurring gotcha from the memory files)."""
from dragontag.app.tagging.partial import write_album_link_tags


class _FakeMP4:
    """Stands in for mutagen.mp4.MP4 — no encoder can synthesize a real
    M4A in the test env, and only the tag-dict logic is under test."""

    store: dict = {}

    def __init__(self, _path):
        self.tags = _FakeMP4.store

    def save(self):
        pass


def _patch_mp4(monkeypatch, initial: dict):
    _FakeMP4.store = dict(initial)
    monkeypatch.setattr("mutagen.mp4.MP4", _FakeMP4)
    return _FakeMP4


def test_mp4_zero_totals_preserve_existing(tmp_path, monkeypatch):
    p = tmp_path / "song.m4a"
    p.write_bytes(b"\x00")
    fake = _patch_mp4(monkeypatch, {"trkn": [(3, 12)], "disk": [(1, 2)]})

    write_album_link_tags(
        p, album="X", album_artist=None,
        track_total=0, disc_total=0,          # 0 = unknown, not "write 0"
        mb_album_id=None, mb_release_group_id=None,
    )
    assert fake.store["trkn"] == [(3, 12)]
    assert fake.store["disk"] == [(1, 2)]


def test_mp4_real_totals_still_written(tmp_path, monkeypatch):
    p = tmp_path / "song.m4a"
    p.write_bytes(b"\x00")
    fake = _patch_mp4(monkeypatch, {"trkn": [(3, 12)]})

    write_album_link_tags(
        p, album="X", album_artist=None,
        track_total=10, disc_total=None,
        mb_album_id=None, mb_release_group_id=None,
    )
    assert fake.store["trkn"] == [(3, 10)]     # number half preserved
