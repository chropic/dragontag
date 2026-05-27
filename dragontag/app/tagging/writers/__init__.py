"""Tag-writer dispatch table.

Each audio format gets its own module that knows how to translate the
canonical ``TrackTags`` into the format's native tag system. We pick the
writer based on the file extension because mutagen's auto-detection picks
on file content but we want explicit control over the tag container chosen
(e.g. ``.m4a`` could be MP4 audio + MP4 tags, never something else).
"""
from __future__ import annotations

from pathlib import Path

from ...config import settings
from ..schema import TrackTags


def write_tags(path: Path, tags: TrackTags) -> None:
    """Dispatch to the format-specific writer based on file extension.

    Raises ``ValueError`` for any extension the app hasn't claimed support
    for — that's preferable to silently doing nothing.
    """
    ext = path.suffix.lower().lstrip(".")
    s = settings()
    sep = s.separators  # snapshot now so a settings save mid-write doesn't shift things

    # Zero out any fields the user has opted to skip. Writers already omit
    # None / empty-list / False values, so this cleanly suppresses them.
    for field_name in s.skip_fields:
        if not hasattr(tags, field_name):
            continue
        current = getattr(tags, field_name)
        if isinstance(current, list):
            setattr(tags, field_name, [])
        elif isinstance(current, bool):
            setattr(tags, field_name, False)
        else:
            setattr(tags, field_name, None)

    if ext == "flac":
        from .flac import write as f
        f(path, tags, sep)
    elif ext == "mp3":
        from .mp3 import write as f
        f(path, tags, sep)
    elif ext in ("m4a", "mp4", "m4b"):
        from .mp4 import write as f
        f(path, tags, sep)
    elif ext == "wav":
        from .wav import write as f
        f(path, tags, sep)
    else:
        raise ValueError(f"Unsupported audio extension: {ext}")
