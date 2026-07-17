---
name: conventions
description: Coding conventions, UI terminology, tag-schema rules. Read before generating code or templates.
metadata:
  type: feedback
---

# Conventions

## Terminology

- **UI-facing**: "Library" (singular: "a library"). Settings/buttons say "Scan library",
  "Organize library", "Individual library actions".
- **Internal identifiers**: keep the historical `LibraryFolder` model, `folder_id` query/form
  params, and route paths (e.g. `/library/folders`). No migration just for naming.
- "Folder" in user-facing strings is reserved for OS-level directories (the drop folder, an
  album folder on disk).

**Why:** A UI rename to "Library" landed in the 05.27.2026 sweep; the schema rename was
deliberately deferred to avoid Alembic churn.
**How to apply:** When editing templates or copy, prefer "Library"; when touching Python
identifiers, keep `folder` / `folder_id`.

## Python style

- `from __future__ import annotations` at module top.
- Triple-quoted module docstrings describing **why the module exists**, not just what it does —
  every module has one; match the register.
- Comments only for non-obvious *why* (invariants, contracts, trade-offs). No comments narrating
  what the next line does, no "fixed in PR#…" annotations.
- Lazy imports inside functions when the module is heavy (Pillow, requests, mutagen submodules)
  or would create a cycle (`from .config import settings` inside functions in `notify.py`,
  `scheduler.py`) — already the norm in `pipeline.py` and `library/actions.py`.
- Naive-UTC datetimes only (`timeutil.now_utc()`); user-facing rendering via `main._local_tz`.
- Exceptions: broad `except Exception` is accepted at thread/loop/route boundaries where one
  failure must not kill the worker (annotated `# noqa: BLE001` where the linter cares), but
  results must be reported honestly — never turn a failure into a success message.

## Tag schema

- Canonical shape lives in `dragontag/app/tagging/schema.py`.
- **Multi-value fields are written as native multiple values** — one Vorbis comment / ID3v2.4
  multi-value / MP4 list entry per value (ARTIST, ALBUMARTIST, ARTISTS, GENRE, sorts,
  composer/conductor/lyricist/arranger, LABEL, ISRC, MB artist-id lists). `to_vorbis` returns
  `dict[str, str | list[str]]` and the writers pass lists through. Navidrome/Picard split these
  correctly — a single `"a//b//c"` string does not.
- The per-tag `Separators` (`//` for ARTIST/`album_artist`, `;` elsewhere) are kept for
  compatibility but are a **fallback only** for those fields; they no longer pre-join. (The
  settings UI still exposes them.)
- `album_artist` stays the lowercase Vorbis key by convention; the names come from
  `TrackTags.album_artists`.
- Track/disc **totals of 0 mean "unknown"** — skipped entirely (truthiness gate), never written
  as a literal `"0"`. The `NN/TT` combined field, the separate `TRACKTOTAL`/`TOTALTRACKS` pair,
  and the MP4 writer all agree on this.
- MP4 freeform atoms always use `----:com.apple.iTunes:NAME` (bytes values) to stay
  Picard-compatible; ID3 uses `TXXX:` descs + `UFID:http://musicbrainz.org` for the recording id.
- New fields must be written across **all four** writers (FLAC, MP3/WAV ID3, MP4) — partial
  coverage breaks the format-agnostic guarantee. All in-place writes go through
  `writers/_atomic.atomic_inplace`.

## Foldering

- Destination paths are built in `library/paths.py`. The artist folder segment goes through
  `primary_artist()`: it prefers `album_artist_display` over `artist_display`, **always** strips
  `feat./ft./featuring …` guests, and only reduces a multi-artist credit ("A & B") to its first
  artist when the user sets `settings().folder_artist_split_separators` (empty by default).
  Slashes (`/`, `//`) are never split. Don't reintroduce feat./guest strings into folder names.
- **Convergence beats canonical naming.** `_reuse_folded_dir` makes ingest reuse whatever album
  folder already exists — a case/punct variant, or (album level, `edition_fold=True`, gated on
  `settings().fold_edition_suffixes`) an edition-suffix twin (`Afraid - Single` → existing
  `Afraid`). It never *renames* to a canonical form; the `cleanup_library` action merges/renames
  twins on disk later. Artist folders are never edition-folded ("The EP" is a real name).
- **Quarantine layout.** `cleanup_library` never deletes: dead folders and leftover non-audio go to
  `<quarantine_root>/<utc-ts>/<path-relative-to-library-root>` (`quarantine_root` =
  `settings().quarantine_path` or `<lib>/.dragontag-trash`), and that root is appended to
  `settings().scan_exclude_dirs`. Audio files are never quarantined.
- Cover art is per-release: the release-group fallback (`fetch_for_release_group`) is gated
  behind `settings().cover_allow_release_group_fallback` (default off) to avoid one shared image
  bleeding across editions.
- Dashboard explicit/lyrics counts read `Track.advisory` / `Track.has_lyrics`; both must be
  populated wherever a file's tags are read or written (scanner, pipeline, bulk routes) or the
  counters silently read 0.

## Templates

- **Visual design:** the terminal/TUI look is a deliberate, cohesive signature — read
  [[design]] (and its source [[slop]]) before adding or restyling any UI. Keep the near-black +
  phosphor-green + monospace + zero-radius identity; no gradients, no gratuitous glows, no
  chip-around-every-noun.
- Extend `base.html`. Set `{% block title %}dragontag | {Page}{% endblock %}` and pass
  `active_page` from the route. HTMX fragments are `_`-prefixed files.
- Buttons that mutate state must POST to a route, never GET.
- Destructive actions need an inline `onsubmit="return confirm(...)"` prompt.
- **User strings in URL contexts need `| urlencode`** — Jinja autoescape covers HTML entities
  only, not `&`/`#`/`+` inside an href query string.
- Use the `hint(text)` macro in `settings.html` for the inline muted description under a field
  (replaced the old hover-tooltip `tip()`); pass plain text only.
- Global keyboard shortcuts go through the `dtKeys` registry defined in `base.html`
  (`dtKeys.register(key, fn)`); per-page bindings live in each template's own `<script>` block.
  `dtKeys` returns early on any `metaKey`/`ctrlKey`/`altKey` combo, so modified shortcuts need an
  independent `document.addEventListener('keydown', ...)`. Every key advertised in a page's
  `{% block statusbar %}` must actually be wired — no dangling hints.
- ASCII art / banner content must stay ASCII/Latin-1 — the vendored font subsets lack
  box-drawing glyphs and a fallback glyph breaks the monospace column grid.
- After template class changes: `bash frontend/build_css.sh` (compiled `app.css` is committed).

## Routes

- All authenticated routes take `_: None = Depends(require_auth)` as the second parameter.
- Routes that kick off long work run it via `tasks.run_task`/`run_chain` (tracked Job + progress
  bar) and return `_toast_response(redirect_url, message)` immediately.
- Form fields the UI may omit (unchecked checkboxes) must be typed `str | None = Form(None)`
  and coerced with `bool(...)`.
- Uploads: stream in 1 MiB chunks (`_read_upload_capped` for bounded reads); clean up partial
  files on failure.
