"""Capture and restore a file's tag state so a tag-write can be undone.

The pipeline overwrites tags destructively — every writer clears the existing
tag set before writing the canonical one. To support *revert*, we snapshot a
file's tags **before** the write and can rewrite them later. Snapshots are
JSON-safe (``{"format": ext, "tags": {key: [values]}}``) so they persist in the
``FileChange.original_tags_json`` column.

Coverage: text/string tags for FLAC (Vorbis comments), MP3/WAV (ID3 text
frames + ``TXXX``) and MP4 (atoms). Embedded binary blocks — cover art,
arbitrary binary frames — are intentionally **not** captured: revert restores
tags in place, not the file byte-for-byte (a documented limitation).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .writers._atomic import atomic_inplace

_ID3_EXTS = ("mp3", "wav")
_MP4_EXTS = ("m4a", "mp4", "m4b")


def capture(path: Path) -> dict[str, Any]:
    """Return a JSON-safe snapshot of ``path``'s current tags.

    Never raises: a snapshot failure must not break the pipeline, so on any
    error we return an empty snapshot (revert then simply has nothing to do).
    """
    ext = path.suffix.lower().lstrip(".")
    try:
        if ext == "flac":
            return {"format": "flac", "tags": _capture_flac(path)}
        if ext in _ID3_EXTS:
            return {"format": ext, "tags": _capture_id3(path, ext)}
        if ext in _MP4_EXTS:
            return {"format": "mp4", "tags": _capture_mp4(path)}
    except Exception:
        return {"format": ext, "tags": {}}
    return {"format": ext, "tags": {}}


def restore(path: Path, snapshot: dict[str, Any]) -> None:
    """Rewrite ``path``'s text tags from a snapshot produced by :func:`capture`."""
    if not snapshot:
        return
    fmt = snapshot.get("format")
    tags = snapshot.get("tags") or {}
    if fmt == "flac":
        _restore_flac(path, tags)
    elif fmt in _ID3_EXTS:
        _restore_id3(path, tags, fmt)
    elif fmt == "mp4":
        _restore_mp4(path, tags)


# ----- FLAC / Vorbis -----
def _capture_flac(path: Path) -> dict[str, list[str]]:
    from mutagen.flac import FLAC

    audio = FLAC(str(path))
    if audio.tags is None:
        return {}
    out: dict[str, list[str]] = {}
    # VComment is an iterable of (key, value) pairs and repeats a key per value,
    # so grouping here preserves genuine multi-value fields.
    for key, value in audio.tags:
        out.setdefault(key, []).append(str(value))
    return out


def _restore_flac(path: Path, tags: dict[str, list[str]]) -> None:
    from mutagen.flac import FLAC

    with atomic_inplace(path) as tmp:
        audio = FLAC(str(tmp))
        # Clear vorbis comments only (leaves PICTURE blocks intact). Use the
        # in-memory clear to avoid the extra on-disk write ``delete()`` does.
        if audio.tags is None:
            audio.add_tags()
        else:
            audio.tags.clear()
        for key, vals in tags.items():
            audio[key] = list(vals)
        audio.save()


# ----- ID3 (MP3 / WAV) -----
def _open_id3(path: Path, ext: str):
    if ext == "wav":
        from mutagen.wave import WAVE

        return WAVE(str(path))
    from mutagen.mp3 import MP3

    return MP3(str(path))


def _capture_id3(path: Path, ext: str) -> dict[str, list[str]]:
    from mutagen.id3 import TextFrame, TXXX

    audio = _open_id3(path, ext)
    if audio.tags is None:
        return {}
    out: dict[str, list[str]] = {}
    for frame in audio.tags.values():
        if isinstance(frame, TXXX):
            out["TXXX:" + frame.desc] = [str(x) for x in frame.text]
        elif isinstance(frame, TextFrame):
            out[frame.FrameID] = [str(x) for x in frame.text]
        # APIC / UFID / COMM and other binary frames are skipped.
    # Embedded lyrics (USLT) are text, not binary, and a user expects a revert
    # to restore them — capture the first frame under a synthetic key so the
    # tag-write+revert cycle doesn't silently drop pre-existing lyrics.
    uslt = audio.tags.getall("USLT")
    if uslt:
        out["USLT"] = [str(uslt[0].text)]
    return out


def _restore_id3(path: Path, tags: dict[str, list[str]], ext: str) -> None:
    from mutagen.id3 import Frames, TXXX, USLT

    with atomic_inplace(path) as tmp:
        audio = _open_id3(tmp, ext)
        if audio.tags is None:
            audio.add_tags()
        # Preserve embedded cover art (APIC) across the clear, the same way
        # the FLAC/MP4 restorers keep PICTURE/covr intact — text tags are the
        # only thing this snapshot/restore cycle is meant to touch.
        apics = audio.tags.getall("APIC")
        audio.tags.clear()
        for apic in apics:
            audio.tags.add(apic)
        for key, vals in tags.items():
            vals = [str(x) for x in vals]
            if key.startswith("TXXX:"):
                audio.tags.add(TXXX(encoding=3, desc=key[5:], text=vals))
                continue
            if key == "USLT":
                # USLT.text is a single string (with lang/desc), not a text list —
                # restore it explicitly rather than via the generic Frames path.
                if vals:
                    audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=vals[0]))
                continue
            cls = Frames.get(key)
            if cls is None:
                continue
            try:
                audio.tags.add(cls(encoding=3, text=vals))
            except Exception:
                continue
        audio.save()


# ----- MP4 / M4A -----

# Atoms mutagen materializes as a bare bool / a list of ints rather than a
# list of strings. Both directions must special-case them: iterating a bare
# bool raises (which would silently produce an *empty* snapshot via capture()'s
# catch-all), and restoring an int atom from strings makes mutagen's atom
# renderer raise on save.
_MP4_BOOL_ATOMS = ("cpil", "pgap", "pcst")
_MP4_INT_ATOMS = (
    "rtng", "tmpo", "stik", "tvsn", "tves", "hdvd", "shwm",
    "cnID", "atID", "plID", "geID", "sfID", "cmID", "akID",
)


def _capture_mp4(path: Path) -> dict[str, list[str]]:
    from mutagen.mp4 import MP4

    audio = MP4(str(path))
    if audio.tags is None:
        return {}
    out: dict[str, list[str]] = {}
    for key, val in audio.tags.items():
        if key == "covr":
            continue  # binary cover — not snapshotted
        if key in _MP4_BOOL_ATOMS:
            out[key] = ["1" if val else "0"]
        elif key in ("trkn", "disk"):
            out[key] = ["{}/{}".format(*pair) for pair in val]
        elif key in _MP4_INT_ATOMS:
            out[key] = [str(x) for x in val]
        else:
            vals = []
            for x in val:
                vals.append(x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x))
            out[key] = vals
    return out


def _restore_mp4(path: Path, tags: dict[str, list[str]]) -> None:
    from mutagen.mp4 import MP4, MP4FreeForm

    with atomic_inplace(path) as tmp:
        audio = MP4(str(tmp))
        if audio.tags is None:
            audio.add_tags()
        t = audio.tags
        covr = t.get("covr")  # preserve the embedded cover across the rewrite
        t.clear()
        if covr is not None:
            t["covr"] = covr
        for key, vals in tags.items():
            if key in _MP4_BOOL_ATOMS:
                t[key] = bool(vals) and vals[0] == "1"
            elif key in ("trkn", "disk"):
                pairs = []
                for s in vals:
                    a, _, b = str(s).partition("/")
                    pairs.append((int(a or 0), int(b or 0)))
                t[key] = pairs
            elif key in _MP4_INT_ATOMS:
                t[key] = [int(x) for x in vals]
            elif key.startswith("----"):
                t[key] = [MP4FreeForm(str(s).encode("utf-8")) for s in vals]
            else:
                t[key] = [str(s) for s in vals]
        audio.save()
