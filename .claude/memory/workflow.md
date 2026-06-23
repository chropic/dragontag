---
name: workflow
description: Branching, testing, CHANGELOG, and PR discipline
metadata:
  type: feedback
---

# Workflow

## Branching

- Default branch: `main`. Always work on a topic branch (`task/<slug>` or `claude/<slug>`).
- Open PRs against `main`. CI runs `pytest -v` + a GHCR Docker build.
- Never commit unless asked; never push to `main` directly.

## Tests

- `pytest -v` (no network, no real MB calls). Add tests under `tests/test_*.py` matching the existing style.
- New tests are required for: any logic in `tagging/`, `identify/`, `library/paths.py`, or `library/organizer.py`. UI changes don't need automated tests but should be smoke-tested in the browser.

## CHANGELOG

- The top of `CHANGELOG.md` has a current `## Unreleased — <sweep name>` block. New work appends bullets here under Added / Changed / Fixed / Removed.
- Older history was consolidated in the 05.27.2026 sweep — do **not** re-expand it.
- File list at the bottom of the Unreleased section enumerates touched paths.

## Settings changes

A new user-editable setting touches **four** files:
1. `dragontag/app/config.py` — field on `UserSettings`.
2. `dragontag/app/web/templates/settings.html` — form input + `hint(text)` macro line.
3. `dragontag/app/main.py` — `Form(...)` parameter in `settings_update` and entry in the `patch` dict.
4. Whatever consumes the setting (usually `ingest/pipeline.py`).

Forgetting any one of these breaks save-and-reload silently — always touch all four.

## PRs

- Title: short imperative (`feat:`, `fix:`, etc.). Body: Summary + Test plan checklist.
- Use the GH CLI: `gh pr create --title ... --body ...` with a HEREDOC for the body.
