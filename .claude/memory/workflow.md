---
name: workflow
description: Dev environment, branching, testing, CHANGELOG, per-commit versioning, and PR discipline
metadata:
  type: feedback
---

# Workflow

## Dev environment

- **Python ≥ 3.12 is mandatory** (`pyproject.toml` `requires-python`). On a machine whose
  default `python` is 3.11, `pip install -e .` fails late with a resolver message
  ("requires a different Python") — create the venv from `python3.12` explicitly:
  `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- `[dev]` extra includes pytest and httpx (route tests import `fastapi.testclient`, which
  needs httpx).
- Run the app locally with the env vars shown in README "Development"; never run app code
  without `DRAGONTAG_*_PATH` overrides or it writes to real `/config`, `/library`, `/drop`.
- After editing templates or `frontend/app.input.css`, run `bash frontend/build_css.sh` to
  rebuild the committed `dragontag/app/web/static/app.css` (downloads the Tailwind standalone
  CLI; no Node needed). Commit the rebuilt css with the template change.

## Branching

- Default branch: `main`. Always work on a topic branch (`task/<slug>` or `claude/<slug>`).
- Open PRs against `main`. Stacked work: base the PR on the branch it depends on; GitHub
  retargets to `main` when the parent merges.
- Never commit unless asked; never push to `main` directly.
- CI (`.github/workflows/ci.yml`): `pytest -v` on Python 3.12 for every PR; the Docker/GHCR
  build job runs only on pushes to `main` and `v*.*.*` tags.

## Tests

- `pytest -v` — no network, no real MB calls, whole suite runs in seconds. See [[testing]] for
  layout, conftest behavior, and per-subsystem patterns.
- New tests required for logic changes in `tagging/`, `identify/`, `library/paths.py`,
  `library/organizer.py`, `library/revert.py`, `tasks.py`, and the pipeline. Every bug fix gets
  a regression test. UI changes: smoke-test in the browser.

## CHANGELOG

- The top of `CHANGELOG.md` has a current WIP block (`## WIP — <name>`); new work appends
  bullets under Added / Changed / Fixed / Removed **with a dated sub-heading for sweeps**
  (e.g. `### Fixed (core/library/web bug sweep — 2026-07-08)`).
- Bullet style: **bold one-line lead-in** — then the mechanism, failure mode, and fix, ending
  with the touched files in parens. Match the existing entries' density.
- Older history was consolidated (05.27.2026) — do **not** re-expand it. The HTML comment at the
  top of the file states this for agents.

## Settings changes

A new user-editable setting touches **four** places (forgetting any one breaks save-and-reload
silently — always touch all four):
1. `dragontag/app/config.py` — field on `UserSettings` (+ a validator if a bad value can break
   the pipeline; see the filename-template validators).
2. `dragontag/app/web/templates/settings.html` — form input + `hint(text)` macro line.
3. `dragontag/app/main.py` — `Form(...)` parameter in `settings_update` and entry in the `patch`
   dict (checkboxes: `str | None = Form(None)` + `bool(...)`).
4. Whatever consumes the setting (usually `ingest/pipeline.py` or the relevant module).

## Versioning

- **Every commit bumps the patch version.** A tracked git hook (`.githooks/pre-commit`) runs
  `scripts/bump_version.py`, which increments `PATCH` (`X.Y.Z` → `X.Y.Z+1`) across
  `pyproject.toml` and both `__init__.py` files and re-stages them. Enable once per clone:
  `git config core.hooksPath .githooks`. Bump `MAJOR`/`MINOR` by hand at milestones. Full
  detail in `docs/VERSIONING.md`.
- The hook skips merge commits; `git commit --no-verify` skips a single bump if ever needed.
- Don't hand-edit the three version files to different values — they must stay in lockstep
  (the bump script re-syncs them from `pyproject.toml`, the source of truth).

## PRs

- Title: short imperative with a conventional prefix (`feat:`, `fix:`, `docs:` …).
- Body: Summary (grouped by severity/area for sweeps) + Testing section stating the suite result.
- **A PR template exists** (`.github/pull_request_template.md`) — GitHub pre-fills it on new PRs.
  Populate its sections; delete any that don't apply. When creating a PR via the GitHub MCP,
  mirror the template's headings in the body.
- In the remote-agent environment there is no `gh` CLI — use the GitHub MCP tools
  (`mcp__github__create_pull_request` etc.). Locally, `gh pr create` with a HEREDOC body works.
- Draft first when the maintainer hasn't pre-approved the direction.

## Memory upkeep

Part of finishing any non-trivial task: update `.claude/memory/` (new invariants → 
[[architecture]], new bug classes → [[gotchas]], workflow changes → this file) and the
`CHANGELOG.md` WIP block in the same PR.
