---
name: testing
description: Test layout, conftest tricks, how to run the suite, patterns for testing each subsystem, and exact signatures tests keep getting wrong
metadata:
  type: project
---

# Testing

## Running

```bash
python3.12 -m venv .venv                 # MUST be 3.12+ (pyproject requires-python)
.venv/bin/pip install -e ".[dev]"        # includes pytest + httpx (route tests need httpx)
.venv/bin/pytest -q                      # full suite, ~4s, no network, no running app
```

- CI (`.github/workflows/ci.yml`) runs `pytest -v` on Python 3.12, then a GHCR Docker build
  (build job skipped on PRs).
- The suite is deliberately network-free: no real MusicBrainz/AcoustID/LRCLIB calls. Anything
  network-shaped is monkeypatched.

## conftest.py — read before writing any test

`tests/conftest.py` sets `DRAGONTAG_CONFIG_PATH` / `DRAGONTAG_LIBRARY_PATH` /
`DRAGONTAG_DROP_PATH` to a fresh temp dir **at import time**, *before* any app import. This
means:

- All tests share **one SQLite DB** for the whole session — tests must tolerate rows created by
  other tests (filter by the ids you created; don't assert global counts).
- Importing an app module at module scope in a test file is fine (pytest imports conftest first).
  Running app code outside pytest without those env vars writes to real `/config` — don't.

## Per-subsystem patterns

- **DB-touching tests**: use `from dragontag.app.db import session`; create rows directly
  (`Job`, `Track`, `LibraryFolder`, `FileChange`) and commit. `s.refresh(obj)` before reading
  the autoincrement id. See `tests/test_tasks_reaper.py` for the minimal shape.
- **File-writing tests**: real tiny media files are synthesized in fixtures where needed
  (see `test_atomic_writes.py`, `test_writers_multivalue.py`); most logic tests use `tmp_path`
  and plain `write_bytes`.
- **Mover/organizer tests**: monkeypatch `organizer.move` / `revert.move` with a fake returning
  `MoveResult(...)`. **Exact signature** (tests repeatedly get this wrong):
  `MoveResult(moved: bool, destination: Path, conflict: bool = False)` — the field is
  `destination`, not `final_path`/`dest`.
- **Schema tests**: the render method is `TrackTags.to_vorbis(sep)` (takes a separator object or
  string arg) — not `to_vorbis_dict()`. It returns `dict[str, str | list[str]]`.
- **Route tests**: `fastapi.testclient.TestClient` (needs `httpx`); authenticate by monkeypatching
  or via the login form — see `tests/test_routes_*.py` for the established helper style.
- **Async handlers** (`uploads.save_uploads`): call with `asyncio.run(...)` and a duck-typed
  upload object exposing `filename` and `async def read(n)`.
- **Settings-dependent code**: `from dragontag.app.config import settings`; monkeypatch
  attributes on the returned object, or monkeypatch `module.settings` where the module imported
  it. Watch for `from .config import settings` *inside* functions (lazy imports) — patch
  `dragontag.app.config.settings` for those.
- **Timezone tests**: `monkeypatch.setenv("TZ", "America/Los_Angeles")` +
  `time.tzset()`; `scheduler._cron_tz` reads the env var each call so no reimport is needed.
  Restore with `monkeypatch.delenv` + `tzset` in a finally.
- **Thread tests**: register/unregister in `tasks._live_threads` under `tasks._threads_lock`;
  use a `threading.Event`-blocked thread so teardown is deterministic.

## What must have tests (maintainer expectation)

New tests are required for any logic change in `tagging/`, `identify/`, `library/paths.py`,
`library/organizer.py`, `library/revert.py`, `tasks.py`, or the pipeline. UI-only changes don't
need automated tests but should be smoke-checked. Every bug fix gets a regression test where the
behavior is observable (both sweeps followed one-test-file-per-bug-or-theme naming:
`test_<area>_<behavior>.py`).

## Map of existing tests (grouped)

- **Paths/foldering**: `test_paths.py`, `test_disc_folder_template.py`, `test_grammar.py`,
  `test_formatter_initialisms.py`
- **Schema/writers**: `test_schema_vorbis.py`, `test_writers_multivalue.py`,
  `test_atomic_writes.py`, `test_snapshot.py`, `test_cover_overwrite_gate.py`,
  `test_coverart_mime.py`, `test_decompression_bomb_guard.py`
- **Identify**: `test_scoring.py`, `test_scoring_unicode.py`, `test_musicbrainz.py`,
  `test_musicbrainz_credits.py`, `test_existing_tags*.py`, `test_filename_parse_titles.py`,
  `test_artist_split*.py`, `test_genre_*.py`, `test_infer_release_type.py`,
  `test_acoustid_timeout.py`, `test_album_consistency*.py`, `test_incomplete_album.py`,
  `test_release_consensus.py` (near-tie release preference; seeds real Track rows for the
  library-majority check), `test_fix_album_splits.py` (wav fixtures + monkeypatched
  `mbq.fetch_release`/`assemble_tags`/`coverart.fetch_for_release` — the pattern for testing
  any MB-backed library action offline)
- **Ingest**: `test_watcher_settle.py`, `test_pipeline_dry_run_shortcircuit.py`,
  `test_pipeline_guard.py`, `test_routes_upload_retag.py`
- **Library ops**: `test_mover*.py`, `test_organize_cleanup.py`, `test_scan_filters.py`,
  `test_filelock.py`, `test_library_actions_new.py`, `test_resolve_conflict_indexes_track.py`
- **Core/infra**: `test_tasks_reaper.py`, `test_config_validators.py`, `test_config_atomic.py`,
  `test_backup_restore_crossfs.py`, `test_cron_describe.py`, `test_net_ssrf.py`,
  `test_network_timeout_live.py`
- **Routes/UI**: `test_routes_*.py`
- **Bug-sweep regression bundles**: `test_bug_sweep_core.py` (notify contract, byte cap,
  logsetup, zero totals, null credit, local-time cron), `test_bug_sweep_rollbacks.py`
  (organizer/move-back rollback honesty, reaper thread-liveness, upload partial cleanup)
