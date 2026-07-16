"""Library destination path computation.

Produces paths in the layout::

    <library_root>/<album_artist>/<album>/[Disc N/]<rendered_filename>

The ``Disc N`` subfolder only appears for multi-disc releases (``disc_total > 1``).
Filenames are rendered from user-configurable templates so users can encode
disc/track prefixes however they prefer.

Sanitization is intentionally conservative: characters illegal on Windows
(the strictest mainstream FS) become ``_``, trailing dots/spaces are stripped,
reserved device names are defused, and exotic dash/quote codepoints are
normalized to their ASCII equivalents. Diacritics, parentheses, brackets and
non-Latin scripts are all preserved — aggressive sanitization mangles
non-Latin artist names.
"""
from __future__ import annotations

import os
import re
import threading
import unicodedata
from pathlib import Path

from ..config import env, settings
from ..tagging.schema import TrackTags


# Windows-forbidden chars (the union covers all major filesystems).
_FORBIDDEN = set('<>:"/\\|?*\0')

# Punctuation normalized in *written* names (folder/file segments). Distinct
# from _QUOTE_DASH_FOLD below, which is comparison-only and also folds ×→x,
# strips ®™© and casefolds — none of which may leak into on-disk names.
# Exotic dash/quote codepoints (U+2010 from MusicBrainz credits, en/em dashes)
# break ASCII lookups and confuse SMB clients, so generated names use the
# plain ASCII equivalents. Diacritics are deliberately NOT touched.
_NAME_PUNCT_NORMALIZE = str.maketrans(
    {
        "‘": "'", "’": "'", "‛": "'",   # curly single quotes
        "“": '"', "”": '"', "„": '"',   # curly double quotes
        "‐": "-", "‑": "-", "‒": "-",   # hyphen variants
        "–": "-", "—": "-", "−": "-",   # en/em dash, minus
    }
)

# Invisible characters that must never survive into a path segment.
_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿­]")

# Windows reserved device names (case-insensitive, applies to the stem).
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{base}{n}" for base in ("COM", "LPT") for n in range(1, 10)
}


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


_EDITION_SUFFIX_RE = re.compile(
    r"\s*[\(\[]\s*(deluxe|remaster(?:ed)?|expanded|anniversary|bonus track|special|"
    r"explicit|clean|single|ep|mono|stereo)[^)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)

# iTunes-style *unparenthesized* trailing edition markers: "Album - Single",
# "Album - EP" (any dash flavour). MusicBrainz names the same release group
# without the suffix, so these are the same album under two source spellings.
_TRAILING_EDITION_RE = re.compile(r"\s*[-–—]\s*(single|ep)\s*$", re.IGNORECASE)

# A dash left dangling by sanitization ("…Friends–", "Album -").
_DANGLING_DASH_RE = re.compile(r"\s*[-–—]+\s*$")


def strip_edition_suffixes(album: str) -> str:
    """Remove parenthesized and iTunes-style trailing edition markers plus any
    dangling dash. Applied before folding so the offline album key groups the
    ``X`` / ``X - Single`` / ``X (Deluxe)`` / ``…Friends–`` variants together."""
    a = _EDITION_SUFFIX_RE.sub("", album)
    a = _TRAILING_EDITION_RE.sub("", a)
    a = _DANGLING_DASH_RE.sub("", a)
    return a


def album_fold_key(name: str) -> str:
    """Fold an album name after stripping edition suffixes — ``Afraid``,
    ``Afraid - Single`` and ``Afraid (Deluxe)`` share one key. Returns an empty
    string when the name is only an edition marker (``(Deluxe)``), so callers
    can refuse to merge such folders into an arbitrary sibling."""
    return fold_text(strip_edition_suffixes(name))

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

    * NFC normalization only (never NFKC — that would mangle names like
      ``Varg²™``; compatibility folding belongs to :func:`fold_text`, which is
      comparison-only).
    * Zero-width/soft-hyphen characters are stripped.
    * Exotic dashes/curly quotes become their ASCII equivalents
      (``_NAME_PUNCT_NORMALIZE``); diacritics are preserved.
    * Forbidden chars become ``_``.
    * Trailing dots/spaces are stripped (Windows rejects them).
    * Windows reserved device names (``CON``, ``COM1``…) get ``_`` appended.
    * An all-junk input (e.g. ``"..."``) collapses to ``"_"`` so we never
      hand back an empty segment.
    """
    cleaned = unicodedata.normalize("NFC", name)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = cleaned.translate(_NAME_PUNCT_NORMALIZE)
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in cleaned)
    cleaned = cleaned.rstrip(". ").strip()
    if cleaned.split(".")[0].upper() in _WIN_RESERVED:
        cleaned += "_"
    return cleaned or "_"


def _dir_has_audio(d: Path) -> bool:
    """True if any supported audio file exists anywhere beneath ``d``."""
    from ..ingest.pipeline import SUPPORTED_EXTS  # lazy: pipeline imports paths at module level

    for _dp, _dn, fns in os.walk(d):
        if any(Path(f).suffix.lower() in SUPPORTED_EXTS for f in fns):
            return True
    return False


class DestinationUnresolved(Exception):
    """The case-insensitive sibling scan of an existing parent failed (I/O
    error). Creating the wanted-case directory anyway could mint a case-variant
    twin next to an existing folder we couldn't see — the caller must not
    proceed with this destination."""

    def __init__(self, parent: Path, wanted: str) -> None:
        super().__init__(f"could not scan {parent} to resolve segment {wanted!r}")
        self.parent = parent
        self.wanted = wanted


# One global lock serializing destination resolution + directory creation.
# path_lock keys on the *file* path, so two files of the same artist processed
# by different threads never serialize on it — this lock is what makes the
# scandir-based case-insensitive reuse and the mkdir that follows atomic
# across the whole process. Contention is negligible (single ingest worker
# plus occasional task threads; a scandir is cheap). Do not shard.
_dir_resolve_lock = threading.Lock()


def _reuse_folded_dir(parent: Path, wanted: str, *, edition_fold: bool = False) -> str:
    """Return an existing sibling of ``parent/wanted`` that folds equal to it.

    When the exact ``wanted`` directory is absent but a sibling directory
    compares equal under :func:`fold_text` (same name modulo case, curly
    quotes, dash flavour, ® marks), reuse that sibling's *exact on-disk name*.
    This makes ingest converge on whatever artist/album folder already exists
    rather than minting a second case-variant next to it (``Afraid`` beside
    ``afraid``). Fold-equality only — never fuzzy — because a false merge would
    move a file into the wrong artist's folder.

    When ``edition_fold`` is set and ``settings().fold_edition_suffixes`` is on,
    a second pass also reuses a sibling that matches after edition suffixes are
    stripped (:func:`album_fold_key`), so ``Afraid - Single`` ingests into an
    existing ``Afraid``. This is convergence, not canonicalization: we reuse
    whatever folder already exists even if its name carries a suffix — the
    Cleanup action merges/renames twins later. Among several suffix-fold
    candidates, prefer one that actually contains audio, then the "base" folder
    whose name equals its own stripped form, then the lexicographically
    smallest for determinism. Never applied to artist folders ("The EP",
    "Single" are legitimate artist-name substrings).

    One ``os.scandir`` per level; nothing is cached (single-user, cheap). A
    missing parent (fresh library — nothing to collide with) degrades to
    ``wanted`` unchanged, but an I/O error while scanning an *existing* parent
    is fail-closed: it raises :class:`DestinationUnresolved` instead of
    pretending no sibling exists, because "pretend and create ``wanted``" is
    exactly how case-variant twins were minted on flaky network shares.
    """
    try:
        parent_exists = parent.exists()
    except OSError as e:
        raise DestinationUnresolved(parent, wanted) from e
    if not parent_exists:
        return wanted
    try:
        if (parent / wanted).exists():
            return wanted
        target = fold_text(wanted)
        if not target:
            return wanted
        do_edition = edition_fold and settings().fold_edition_suffixes
        wanted_ekey = album_fold_key(wanted) if do_edition else ""
        edition_candidates: list[str] = []
        with os.scandir(parent) as it:
            for entry in it:
                if entry.name == wanted or not entry.is_dir():
                    continue
                if fold_text(entry.name) == target:
                    return entry.name  # exact case/punct fold wins immediately
                if wanted_ekey and album_fold_key(entry.name) == wanted_ekey:
                    edition_candidates.append(entry.name)
        if edition_candidates:
            best = min(
                edition_candidates,
                key=lambda n: (
                    not _dir_has_audio(parent / n),          # audio-bearing first
                    strip_edition_suffixes(n) != n,          # base (unsuffixed) name next
                    n,                                       # then deterministic
                ),
            )
            return best
    except OSError as e:
        raise DestinationUnresolved(parent, wanted) from e
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
    ensure_dirs: bool = False,
) -> Path:
    """Return the full absolute destination path for a tagged track.

    ``library_root`` overrides ``env().library_path`` when multiple library
    folders are configured. Existing callers that pass no keyword argument
    continue to get the env default.

    With ``ensure_dirs=True`` the destination's parent directories are created
    before returning, *inside the same critical section* as the case-folded
    sibling resolution — resolve→create is atomic across threads, so two
    concurrent callers can never mint case-variant twin directories. Callers
    that only preview a destination keep the default ``ensure_dirs=False``
    (no mkdir; the lock is still taken so answers stay consistent).

    Raises :class:`DestinationUnresolved` when the sibling scan of an existing
    parent fails — proceeding could create a case twin, so callers must treat
    the file as unplaceable (queue for review / skip) rather than move it.
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

    with _dir_resolve_lock:
        # Converge on an existing folder that differs only by case/punctuation
        # instead of creating a duplicate next to it (the root cause of the
        # Windows-side duplicate-listing problem). Artist level first, then
        # album under the (possibly reused) artist directory.
        artist_seg = _reuse_folded_dir(base, artist_seg)
        album_seg = _reuse_folded_dir(base / artist_seg, album_seg, edition_fold=True)

        parts = [base, artist_seg, album_seg]
        if (tags.disc_total or 1) > 1 and tags.disc is not None:
            disc_folder = settings().multidisc_folder_template.format(
                disc=tags.disc, disctotal=tags.disc_total
            )
            parts.append(sanitize_segment(disc_folder))

        filename = render_filename(tags, source_ext)
        dest = Path(*parts) / filename
        # Defence-in-depth: ``sanitize_segment`` already neutralizes path
        # separators and traversal sequences (mapping them to "_"), but verify
        # the fully resolved destination still lives under the library root
        # before any caller writes to it — so a future template/sanitizer
        # change can never let a crafted tag value land a file outside the
        # library.
        base_resolved = base.resolve()
        try:
            dest.resolve().relative_to(base_resolved)
        except ValueError as e:
            raise ValueError(
                f"destination {dest} escapes library root {base_resolved}"
            ) from e
        if ensure_dirs:
            dest.parent.mkdir(parents=True, exist_ok=True)
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
