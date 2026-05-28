---
name: conventions
description: Coding conventions, UI terminology, tag-schema rules. Read before generating code or templates.
metadata:
  type: feedback
---

# Conventions

## Terminology

- **UI-facing**: "Library" (singular: "a library"). Settings/buttons say "Scan library", "Organize library", "Individual library actions".
- **Internal identifiers**: keep the historical `LibraryFolder` model, `folder_id` query/form params, and route paths (e.g. `/library/folders`). No migration just for naming.
- "Folder" in user-facing strings is reserved for OS-level directories (the drop folder, an album folder on disk).

**Why:** A UI rename to "Library" landed in the 05.27.2026 sweep; the schema rename was deliberately deferred to avoid Alembic churn.
**How to apply:** When editing templates or copy, prefer "Library"; when touching Python identifiers, keep `folder` / `folder_id`.

## Python style

- `from __future__ import annotations` at module top.
- Triple-quoted module docstrings describing why the module exists, not just what.
- Comments only for non-obvious *why* — see [CLAUDE.md tone rules](../../CLAUDE.md) if present, else default: no comments for self-evident code.
- Lazy imports inside functions when the module is heavy (Pillow, requests, mutagen-specific submodules) — already used in `pipeline.py` and `library/actions.py`.

## Tag schema

- Canonical shape lives in `dragontag/app/tagging/schema.py`.
- Multi-value separators: `//` for ARTIST and `album_artist`; `;` for everything else (MB IDs, GENRE, LABEL, ISRC, sorts). User-overridable in `Separators`.
- MP4 freeform atoms always use `----:com.apple.iTunes:NAME` to stay Picard-compatible.
- New fields must be written across **all four** writers (FLAC, MP3/WAV ID3, MP4) — partial coverage breaks the format-agnostic guarantee.

## Templates

- Extend `base.html`. Set `{% block title %}dragontag | {Page}{% endblock %}` and pass `active_page` from the route.
- Buttons that mutate state must POST to a route, never GET.
- Destructive actions need an inline `onsubmit="return confirm(...)"` prompt.
- Use the `tip(...)` macro in `settings.html` for tooltips; pass plain text only (Jinja escapes attribute content).

## Routes

- All authenticated routes take `_: None = Depends(require_auth)` as the second parameter.
- Routes that kick off long work return `_toast_response(redirect_url, message)` and run the work in a daemon thread.
- Form fields the UI may omit (unchecked checkboxes) must be typed `str | None = Form(None)` and coerced with `bool(...)`.
