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

from pathlib import Path

from ..config import env, settings
from ..tagging.schema import TrackTags


# Windows-forbidden chars (the union covers all major filesystems).
_FORBIDDEN = set('<>:"/\\|?*\0')


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


def build_destination(tags: TrackTags, source_ext: str) -> Path:
    """Return the full absolute destination path for a tagged track."""
    base = env().library_path
    # Prefer album_artist (band on the cover) over artist_display
    # (featured-credits string) for the folder name — it keeps "Artist feat.
    # Guest" tracks under the main artist's folder.
    artist_seg = sanitize_segment(
        tags.album_artist_display or tags.artist_display or "Unknown Artist"
    )
    album_seg = sanitize_segment(tags.album or "Unknown Album")

    parts = [base, artist_seg, album_seg]
    if (tags.disc_total or 1) > 1 and tags.disc is not None:
        disc_folder = settings().multidisc_folder_template.format(
            disc=tags.disc, disctotal=tags.disc_total
        )
        parts.append(sanitize_segment(disc_folder))

    filename = render_filename(tags, source_ext)
    return Path(*parts) / filename
