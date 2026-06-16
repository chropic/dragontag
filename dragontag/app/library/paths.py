"""Library destination path computation.

Produces paths in the layout::

    <library_root>/<album_artist>/<album>/[Disc N/]<rendered_filename>

The ``Disc N`` subfolder only appears for multi-disc releases (``disc_total > 1``).
Filenames are rendered from user-configurable templates so users can encode
disc/track prefixes however they prefer.

Sanitization is intentionally minimal: we strip only the characters that are
illegal on Windows (the strictest mainstream FS), plus trailing dots/spaces.
Unicode, parentheses, brackets, etc. are all preserved — they're fine on
ext4 and NTFS, and aggressive sanitization mangles non-Latin artist names.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import env, settings
from ..tagging.schema import TrackTags


# Windows-forbidden chars (the union covers all major filesystems).
_FORBIDDEN = set('<>:"/\\|?*\0')

# Featured-guest markers: everything from "feat./ft./featuring" onward is cut.
# The marker must be preceded by whitespace or an opening bracket so we never
# clip artists whose names merely contain the letters (e.g. "Daft Punk", where
# the "ft" sits mid-word).
_FEAT_RE = re.compile(r"[\s(\[]+(?:feat\.?|ft\.?|featuring)\b.*$", re.IGNORECASE)


def primary_artist(name: str) -> str:
    """Reduce a full artist credit to the primary artist for the folder name.

    * Featured-guest suffixes ("… feat./ft./featuring …") are *always* stripped
      so "Artist feat. Guest" files under the single "Artist" folder.
    * A multi-artist credit ("A & B", "A, B") is reduced to its first artist
      only when the user has opted in via
      ``settings().folder_artist_split_separators``. Slashes are never treated
      as separators, so "AC/DC" and dragontag's own "A//B" join stay combined.

    Falls back to the original name if stripping would leave it empty.
    """
    s = _FEAT_RE.sub("", name).strip()
    seps = [
        c
        for c in (settings().folder_artist_split_separators or "")
        if not c.isspace() and c != "/"
    ]
    if seps:
        pattern = "[" + re.escape("".join(sorted(set(seps)))) + "]"
        s = re.split(pattern, s, maxsplit=1)[0].strip()
    return s or name


def sanitize_segment(name: str) -> str:
    """Make ``name`` safe to use as a single path component.

    * Forbidden chars become ``_``.
    * Trailing dots/spaces are stripped (Windows rejects them).
    * An all-junk input (e.g. ``"..."``) collapses to ``"_"`` so we never
      hand back an empty segment.
    """
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name)
    cleaned = cleaned.rstrip(". ").strip()
    return cleaned or "_"


def render_filename(tags: TrackTags, ext: str) -> str:
    """Render the filename half of the destination using the user template."""
    s = settings()
    multidisc = (tags.disc_total or 1) > 1
    tmpl = s.filename_template_multidisc if multidisc else s.filename_template_single
    return sanitize_segment(
        tmpl.format(
            track=tags.track or 0,
            disc=tags.disc or 1,
            title=tags.title or "Unknown Title",
            artist=tags.artist_display or "Unknown Artist",
            ext=ext.lstrip("."),
            disctotal=tags.disc_total or 1,
            tracktotal=tags.track_total or 0,
        )
    )


def build_destination(
    tags: TrackTags,
    source_ext: str,
    *,
    library_root: Path | None = None,
) -> Path:
    """Return the full absolute destination path for a tagged track.

    ``library_root`` overrides ``env().library_path`` when multiple library
    folders are configured. Existing callers that pass no keyword argument
    continue to get the env default.
    """
    base = library_root if library_root is not None else env().library_path
    # Prefer album_artist (band on the cover) over artist_display
    # (featured-credits string) for the folder name, then reduce it to the
    # primary artist so "Artist feat. Guest" tracks land under "Artist".
    artist_seg = sanitize_segment(
        primary_artist(
            tags.album_artist_display or tags.artist_display or "Unknown Artist"
        )
    )
    album_seg = sanitize_segment(tags.album or "Unknown Album")

    parts = [base, artist_seg, album_seg]
    if (tags.disc_total or 1) > 1 and tags.disc is not None:
        disc_folder = settings().multidisc_folder_template.format(
            disc=tags.disc, disctotal=tags.disc_total
        )
        parts.append(sanitize_segment(disc_folder))

    filename = render_filename(tags, source_ext)
    dest = Path(*parts) / filename
    # Defence-in-depth: ``sanitize_segment`` already neutralizes path separators
    # and traversal sequences (mapping them to "_"), but verify the fully
    # resolved destination still lives under the library root before any caller
    # writes to it — so a future template/sanitizer change can never let a
    # crafted tag value land a file outside the library.
    base_resolved = base.resolve()
    try:
        dest.resolve().relative_to(base_resolved)
    except ValueError as e:
        raise ValueError(
            f"destination {dest} escapes library root {base_resolved}"
        ) from e
    return dest


def unique_path(p: Path) -> Path:
    """Return ``p`` if it doesn't exist, otherwise append -1, -2, … until free."""
    if not p.exists():
        return p
    i = 1
    while True:
        cand = p.with_stem(f"{p.stem}-{i}")
        if not cand.exists():
            return cand
        i += 1
