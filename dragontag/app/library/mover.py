"""Move a file into the library, with conflict detection.

Conflicts are *not* resolved automatically — they're surfaced through the
review queue so the user explicitly picks replace / rename / skip. The
``cover.jpg`` sidecar writer has its own overwrite policy (size-gated) since
it's an auto-generated sidecar, not the audio file itself.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MoveResult:
    moved: bool
    destination: Path
    conflict: bool = False  # True iff we refused to overwrite an existing file


def move(source: Path, destination: Path, *, overwrite: bool = False) -> MoveResult:
    """Move ``source`` to ``destination``.

    Creates parent directories as needed. ``shutil.move`` handles same-FS
    rename (fast) and cross-FS copy+delete (slow but correct) transparently.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        return MoveResult(moved=False, destination=destination, conflict=True)
    if destination.exists():
        # ``shutil.move`` refuses to overwrite on Windows, so we unlink first.
        destination.unlink()
    shutil.move(str(source), str(destination))
    return MoveResult(moved=True, destination=destination)


def write_cover_jpg(
    folder: Path,
    data: bytes,
    *,
    min_overwrite_pixels: int,
    new_width: int,
) -> Path | None:
    """Save the album cover as ``cover.jpg`` next to the audio files.

    If a cover already exists we only overwrite when the new image's width
    is at least ``min_overwrite_pixels`` — that protects a hand-curated
    high-res cover from being clobbered by a small fingerprint-fallback one.

    Returns the written path, or ``None`` if we skipped (existing cover OK).
    """
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "cover.jpg"
    if target.exists() and new_width < min_overwrite_pixels:
        return None
    target.write_bytes(data)
    return target
