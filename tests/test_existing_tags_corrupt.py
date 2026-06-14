"""M2: a corrupt/truncated audio file must degrade gracefully, not raise.

``existing_tags.read`` is the pipeline's first step; if it raised on a bad
header the whole job would error instead of falling back to filename + MB
search and routing to review.
"""
from dragontag.app.identify import existing_tags


def test_corrupt_flac_returns_empty_clues(tmp_path):
    # A file with a FLAC magic byte sequence but a garbage/truncated body makes
    # mutagen raise; read() should swallow it.
    p = tmp_path / "broken.flac"
    p.write_bytes(b"fLaC" + b"\x00\xff" * 8)
    out = existing_tags.read(p)
    assert out == {"duration": None}


def test_unknown_file_type_returns_empty_clues(tmp_path):
    p = tmp_path / "notaudio.flac"
    p.write_bytes(b"this is plainly not audio")
    out = existing_tags.read(p)
    assert out.get("duration") is None
