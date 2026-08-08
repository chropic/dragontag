# Working in dragontag

This is the short orientation for any coding agent or contributor. Treat the
implementation, tests, and manifests as authoritative; update this guide only
when a durable workflow rule or subsystem boundary changes.

## Product boundaries

dragontag is a self-hosted, Docker-native music tagger and library organizer.
Files enter through the drop folder or web upload, are identified with
MusicBrainz and optional AcoustID fallback, receive a common tag schema plus
artwork and lyrics, and move into the configured library layout. Ambiguous or
unsafe work goes to a browser review queue.

The application is deliberately single-user and single-instance. FastAPI,
Jinja2, HTMX, Alpine.js, SQLite, and local threads are intentional choices.
Do not introduce tenancy, distributed workers, PostgreSQL, or heavy frontend
infrastructure without an explicit product change. Protecting users' audio
files outranks convenience and throughput.

Read [the architecture guide](docs/ARCHITECTURE.md) for the runtime model and
[the invariant guide](docs/INVARIANTS.md) before changing file operations,
tagging, ingest, background work, configuration, or routes. UI work also needs
[the frontend guide](frontend/README.md).

## Start safely

- Inspect `git status` first and preserve unrelated work. Use a topic branch for
  non-trivial changes. Never commit or push unless requested.
- Python 3.12 or newer is required. Create an isolated environment and install
  development dependencies with `python -m venv .venv` followed by
  `.venv/bin/python -m pip install -e ".[dev]"` on POSIX or
  `.venv\Scripts\python -m pip install -e ".[dev]"` on Windows.
- Run tests through the environment's interpreter. The full suite is
  `python -m pytest -q`; it is designed to avoid live network services.
- Do not import or run application code casually with default paths. Outside
  pytest, set `DRAGONTAG_CONFIG_PATH`, `DRAGONTAG_LIBRARY_PATH`, and
  `DRAGONTAG_DROP_PATH` to disposable local directories so nothing writes to
  `/config`, `/library`, or `/drop`. The development command is in
  [README.md](README.md#development).
- Before a requested commit, enable the tracked version hook once with
  `git config core.hooksPath .githooks`. Each non-merge commit bumps the patch
  version; see [docs/VERSIONING.md](docs/VERSIONING.md).

`tests/conftest.py` installs isolated paths before app imports and uses one
session-wide SQLite database. Tests should filter by records they create rather
than assert global row counts. For logic changes, run focused tests first and
then the full suite. Every bug fix needs a regression test when its behavior is
observable.

## Find the source of truth

| Concern | Start here |
|---|---|
| FastAPI routes, auth wiring, settings forms | `dragontag/app/main.py` |
| Deploy-time environment and user settings | `dragontag/app/config.py` |
| Tables, job states, review reasons | `dragontag/app/models.py` |
| Ingest orchestration and worker queue | `dragontag/app/ingest/pipeline.py` |
| Album-level release election | `dragontag/app/ingest/album.py` |
| MusicBrainz, AcoustID, scoring, tag reads | `dragontag/app/identify/` |
| Canonical schema and format writers | `dragontag/app/tagging/` |
| Paths, moves, scans, cleanup, retag, revert | `dragontag/app/library/` |
| Background jobs and scheduled dispatch | `dragontag/app/tasks.py`, `scheduler.py` |
| Templates and compiled styles | `dragontag/app/web/`, `frontend/` |

Search by route, symbol, setting, or test name instead of relying on a static
inventory. `main.py` and `library/actions.py` are intentionally large.

## Non-negotiable contracts

- Audio mutation uses `tagging.writers._atomic.atomic_inplace`; callers also
  hold `library.filelock.path_lock` across read-modify-write or move sequences.
- `library.mover.move` reports conflicts in `MoveResult`; always inspect the
  result. Move lyric sidecars with their audio and report failed rollbacks as
  divergence, never success.
- Create canonical destination directories only through
  `library.paths.build_destination(..., ensure_dirs=True)`. Resolution fails
  closed when an existing parent cannot be scanned.
- Full tag writes require a snapshot and auditable `FileChange`. New conceptual
  tag fields must be implemented consistently across FLAC, MP3, WAV, and MP4.
- All identification paths must preserve the pipeline's dry-run, duplicate,
  review, and `prepare_tags` gates.
- Store naive UTC from `timeutil.now_utc`; convert only for display and cron
  interpretation.
- Long operations run through `tasks.run_task` or `run_chain`. Do not hold an
  SQLite write transaction across a network call.
- State-changing routes use authenticated POST requests. Escape user content in
  hand-built HTML and apply `urlencode` in URL contexts.

The exact contracts and representative tests live in
[docs/INVARIANTS.md](docs/INVARIANTS.md).

## Change discipline

Keep changes scoped and match established conventions in neighboring code.
Update the current WIP section of `CHANGELOG.md` for meaningful changes. A new
setting, tag field, task type, or file-moving path has multiple integration
points; use the recipes in the architecture guide rather than guessing.

After template class changes or edits to `frontend/app.input.css`, run
`bash frontend/build_css.sh` and include the generated
`dragontag/app/web/static/app.css`. All browser assets remain vendored; do not
add CDN dependencies. UI-only work should be smoke-tested in a real browser.

Do not grow these guides into a feature diary. Durable invariants belong in
`docs/INVARIANTS.md`, subsystem boundaries in `docs/ARCHITECTURE.md`, and user
behavior in the README or in-app documentation. Source and regression tests
remain the detailed record.
