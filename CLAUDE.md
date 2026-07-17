# CLAUDE.md — agent orientation for dragontag

Read this first. It is the entry point for AI agents working on this repo; the deep notes live
in `.claude/memory/` and are indexed in [.claude/memory/MEMORY.md](.claude/memory/MEMORY.md).

## What this is

Self-hosted, Docker-native, **single-user** music tagger. A file dropped in `/drop` is identified
against MusicBrainz (AcoustID fallback), tagged with a full Vorbis-style schema, gets cover art +
lyrics embedded, and is moved into `Album Artist/Album/NN. Title.ext` under `/library`. FastAPI +
Jinja2 + HTMX/Alpine UI, SQLite + threads (deliberately no Postgres/celery/multi-tenant anything).

## Fast facts you will otherwise waste time rediscovering

- **Python ≥ 3.12 is required** (`pyproject.toml`). If the system `python` is 3.11, `pip install -e .`
  fails with a confusing resolver message. Make a venv from `python3.12` explicitly.
- Setup + tests: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest -q`.
  Tests need no network and no running app. `httpx` is required for the route tests (it's in `[dev]`).
- `tests/conftest.py` redirects `DRAGONTAG_CONFIG_PATH`/`LIBRARY_PATH`/`DROP_PATH` to a temp dir
  **at import time** — never import app modules in a test before conftest has run (pytest handles
  this; a stray `python -c "import dragontag.app.main"` at repo root writes into `/config`).
- CI (`.github/workflows/ci.yml`): `pytest -v` on 3.12, then a GHCR Docker build (build only runs
  on push to main / version tags, not PRs).
- The stylesheet is **compiled and committed**: after editing templates or `frontend/app.input.css`,
  run `bash frontend/build_css.sh` to regenerate `dragontag/app/web/static/app.css`. New Tailwind
  utility classes silently do nothing until you do.
- All web assets are vendored (fonts, htmx, alpine, app.css) — never add a CDN reference.
- **Every commit bumps the patch version.** A tracked git hook does it, but a fresh clone must
  opt in **once**: `git config core.hooksPath .githooks`. Run that before your first commit or the
  bump silently won't happen. Details: `docs/VERSIONING.md`, hard rule 9 below.

## Hard rules (violating these has caused real bugs — see .claude/memory/gotchas.md)

1. **Any code that mutates an audio file** must go through
   `tagging/writers/_atomic.atomic_inplace(path)` (temp copy → mutate → `os.replace`).
2. **Any code that moves a file or does read-then-write on its tags/location** must hold
   `library/filelock.path_lock(path)`. Known mutators: the ingest worker
   (`ingest/pipeline.py`), revert/move-back (`library/revert.py`), the organizer
   (`library/organizer.py`), every file-touching function in `library/actions.py`
   (including `cleanup_library`'s twin-merge/quarantine moves), and
   `library/retag.apply_match` (shared in-place re-tag). If you add another, lock it.
   Canonical destination directories are created ONLY via
   `paths.build_destination(..., ensure_dirs=True)` (resolve+mkdir under one global lock,
   fail-closed via `DestinationUnresolved`) — never mkdir a library destination yourself, and
   never swallow a directory-scan error into "create it anyway" (that minted case-twin dirs).
3. **`library/mover.move(..., overwrite=False)` does NOT raise on conflict** — it returns
   `MoveResult(moved=False, conflict=True)`. Always check `.moved` / `.conflict`; ignoring the
   result has twice produced "reported success, file actually elsewhere" bugs.
4. **New tag fields go into all four writers** (FLAC, MP3, WAV, MP4) plus `tagging/schema.py`,
   or the format-agnostic guarantee breaks silently.
5. **Datetimes are naive UTC** everywhere in the DB (`timeutil.now_utc()`); anything user-facing
   converts via the display timezone (`main._local_tz()`: `TZ` env → `settings().timezone` → UTC).
   Cron expressions are interpreted in that same display timezone (`scheduler._cron_tz`).
6. **A new user-editable setting touches four places** — `config.UserSettings`, the
   `settings.html` form, `main.py::settings_update` (Form param + patch dict), and the consumer.
   Missing one fails silently on save-and-reload.
7. Buttons that mutate state POST (never GET); destructive ones need a `confirm(...)`;
   authenticated routes take `_: None = Depends(require_auth)`.
8. **Never commit or push without being asked**; always work on a topic branch; update the
   `CHANGELOG.md` WIP section with your changes (style: grouped Added/Changed/Fixed bullets,
   bold lead-in, trailing `(files)` list).
9. **Every commit versions.** The patch segment (`X.Y.Z` → `X.Y.Z+1`) is bumped in lockstep
   across `pyproject.toml` + both `__init__.py` files by the `.githooks/pre-commit` hook —
   enable it once per clone with `git config core.hooksPath .githooks`. Don't hand-edit those
   three files to differing values (the bump script re-syncs them from `pyproject.toml`); don't
   `--no-verify` past the bump except for a deliberate no-bump commit. Bump `MAJOR`/`MINOR` by
   hand at milestones. See `docs/VERSIONING.md`.

## Where things are

| Area | Files |
|---|---|
| Routes (all of them) | `dragontag/app/main.py` (~large; search by route path) |
| Config layering (env → secrets → settings.json) | `dragontag/app/config.py` |
| Ingest pipeline + worker queue | `dragontag/app/ingest/pipeline.py` |
| Tag schema + rendering | `dragontag/app/tagging/schema.py`, `tagging/writers/` |
| Identification | `dragontag/app/identify/` (musicbrainz, acoustid, scoring) |
| Library ops (scan/organize/actions/revert) | `dragontag/app/library/` |
| Background tasks + reaper | `dragontag/app/tasks.py` |
| Cron scheduler | `dragontag/app/scheduler.py` |
| Templates | `dragontag/app/web/templates/` (extend `base.html`) |
| Deep agent notes | `.claude/memory/` — **read the index before non-trivial work** |

## Memory index

- `.claude/memory/project_overview.md` — what the app is, surfaces, goals
- `.claude/memory/architecture.md` — module map, job state machine, threading, invariants
- `.claude/memory/conventions.md` — style, terminology, tag-schema rules, template/route rules
- `.claude/memory/design.md` — the deliberate terminal/TUI identity + anti-slop house rules (read before any UI work)
- `.claude/memory/slop.md` — the full pols.dev anti-slop design law `design.md` distils from
- `.claude/memory/workflow.md` — dev env, tests, CHANGELOG, per-commit versioning, PR discipline
- `.claude/memory/gotchas.md` — bug patterns actually found here; check before writing similar code
- `.claude/memory/testing.md` — test layout, fixtures, how to test each subsystem
- `.claude/memory/user_preferences.md` — how the maintainer likes to work

Update the memory files when you learn something durable (a new invariant, a fixed bug class, a
changed workflow). Keep them factual and dense — they are for agents, not end users.
