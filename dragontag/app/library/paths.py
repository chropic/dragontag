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

import os
import re
import unicodedata
from pathlib import Path

from ..config import env, settings
from ..tagging.schema import TrackTags


# Windows-forbidden chars (the union covers all major filesystems).
_FORBIDDEN = set('<>:"/\\|?*\0')


# Punctuation the folder tree drifts on across sources/OSes: curly vs straight
# quotes, the various Unicode dashes, the multiplication sign used for "x"
# collaborations. Folded to one canonical spelling so two folders that differ
# only by these characters compare equal.
_QUOTE_DASH_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "‛": "'",          # ‘ ’ ‛ → '
        "“": '"', "”": '"', "„": '"',          # “ ” „ → "
        "‐": "-", "‑": "-", "‒": "-",          # ‐ ‑ ‒ → -
        "–": "-", "—": "-", "−": "-",          # – — − → -
        "×": "x",                                        # × → x
    }
)


def fold_text(s: str) -> str:
    """Fold a string for case-, punctuation- and Unicode-insensitive matching.

    NFKC (® ™ fullwidth → compat forms), quote/dash normalization, drop the
    ®/™/© marks entirely, collapse whitespace, casefold. Used to decide whether
    two artist/album folder names are "the same" on a case-insensitive Windows
    view of a case-sensitive Linux volume. Never used to *rename* — only to
    group/compare, so an over-eager fold can never mangle a stored name.
    """
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_QUOTE_DASH_FOLD)
    s = re.sub(r"[®™©]", "", s)
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s


def artist_fold_key(name: str) -> str:
    """Fold an artist credit to its grouping key.

    Order matters: reduce to the primary artist first (strips feat./configured
    separators), *then* fold punctuation/case — so "Artist feat. Guest" and
    "artist" collapse together.
    """
    return fold_text(primary_artist(name))

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


def _reuse_folded_dir(parent: Path, wanted: str) -> str:
    """Return an existing sibling of ``parent/wanted`` that folds equal to it.

    When the exact ``wanted`` directory is absent but a sibling directory
    compares equal under :func:`fold_text` (same name modulo case, curly
    quotes, dash flavour, ® marks), reuse that sibling's *exact on-disk name*.
    This makes ingest converge on whatever artist/album folder already exists
    rather than minting a second case-variant next to it (``Afraid`` beside
    ``afraid``). Fold-equality only — never fuzzy — because a false merge would
    move a file into the wrong artist's folder.

    One ``os.scandir`` per level; nothing is cached (single-user, cheap). Any
    filesystem error (parent doesn't exist yet — the common case on a fresh
    library) degrades to ``wanted`` unchanged.
    """
    try:
        if (parent / wanted).exists():
            return wanted
        target = fold_text(wanted)
        if not target:
            return wanted
        with os.scandir(parent) as it:
            for entry in it:
                if (
                    entry.name != wanted
                    and entry.is_dir()
                    and fold_text(entry.name) == target
                ):
                    return entry.name
    except OSError:
        pass
    return wanted


def render_filename(tags: TrackTags, ext: str) -> str:
    """Render the filename half of the destination using the user template."""
    s = settings()
    # Must mirror build_destination's Disc-folder condition exactly: with
    # disc_total > 1 but no disc number, choosing the multidisc template here
    # (which renders {disc} as a constant 1) while build_destination skips the
    # Disc N folder would give every disc's tracks colliding filenames.
    multidisc = (tags.disc_total or 1) > 1 and tags.disc is not None
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

    # Converge on an existing folder that differs only by case/punctuation
    # instead of creating a duplicate next to it (the root cause of the
    # Windows-side duplicate-listing problem). Artist level first, then album
    # under the (possibly reused) artist directory.
    artist_seg = _reuse_folded_dir(base, artist_seg)
    album_seg = _reuse_folded_dir(base / artist_seg, album_seg)

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
