"""Move a file into the library, with conflict detection.

Conflicts are *not* resolved automatically — they're surfaced through the
review queue so the user explicitly picks replace / rename / skip. The
``cover.jpg`` sidecar writer has its own overwrite policy (size-gated) since
it's an auto-generated sidecar, not the audio file itself.
"""
from __future__ import annotations

import os
import shutil
import tempfile
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
    if destination.exists():
        # Re-tagging a file already at its canonical path resolves
        # ``source == destination`` (the Re-tag / Nuclear batches re-ingest
        # files in place). That's a successful no-op, not a conflict — without
        # this guard every correctly-placed file would be flagged as a
        # destination conflict, and an ``overwrite`` self-move would unlink the
        # file and then fail on the now-missing source.
        same = source == destination
        if not same:
            try:
                same = source.exists() and os.path.samefile(str(source), str(destination))
            except OSError:
                # Source vanished between the exists() check and samefile(), or
                # another FS error — treat as "not the same file" and let the
                # move attempt below surface a precise error.
                same = False
        if same:
            return MoveResult(moved=True, destination=destination)
        if not overwrite:
            return MoveResult(moved=False, destination=destination, conflict=True)
        # ``shutil.move`` refuses to overwrite on Windows, so we unlink first.
        destination.unlink()

    # Capture the source size up front so we can verify the move actually
    # landed every byte (defense-in-depth against a silently-truncated write
    # over a flaky network volume).
    try:
        src_size = source.stat().st_size
    except OSError:
        src_size = None
    shutil.move(str(source), str(destination))
    if src_size is not None:
        dst_size = destination.stat().st_size
        if dst_size != src_size:
            raise OSError(
                f"move verification failed: {destination} is {dst_size} bytes, "
                f"expected {src_size}"
            )
    return MoveResult(moved=True, destination=destination)


def move_lyric_sidecar(old_audio: Path, new_audio: Path) -> bool:
    """Move a matching ``.lrc`` sidecar alongside a renamed/moved audio file.

    Best-effort: never raises and never overwrites an existing target — a
    sidecar miss must not block the audio file's own rename/move.
    """
    src = old_audio.with_suffix(".lrc")
    if not src.exists():
        return False
    dest = new_audio.with_suffix(".lrc")
    if src == dest or dest.exists():
        return False
    try:
        move(src, dest, overwrite=False)
        return True
    except OSError:
        return False


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
    # Write to a temp file in the same dir and atomically swap it in, so a crash
    # mid-write can't truncate an existing cover.jpg.
    fd, tmp = tempfile.mkstemp(dir=str(folder), prefix=".dgcover-", suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
