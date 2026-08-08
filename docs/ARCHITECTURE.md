# Architecture

This document describes stable boundaries and data flow. It intentionally
avoids exhaustive route, setting, and test inventories; search the referenced
symbols for current details.

## Runtime shape

dragontag is one FastAPI process backed by SQLModel and SQLite. `main._startup`
initializes the configuration store and database, applies logging settings,
cleans orphaned atomic-write files, starts one ingest worker, recovers pending
jobs, optionally starts the drop-folder watcher, and starts the scheduler.

The web layer is server-rendered Jinja2 enhanced with HTMX and Alpine.js.
Templates live under `dragontag/app/web/templates`, extend `base.html`, and use
the committed stylesheet in `web/static`. FastAPI's default documentation URLs
are disabled: `/docs` is the user manual, while authenticated OpenAPI and
Swagger surfaces are provided separately. `/health`, login, setup, and static
assets are the intentional unauthenticated surfaces.

SQLite lives at `${DRAGONTAG_CONFIG_PATH}/dragontag.db`. `db.session` supplies
short-lived sessions and the engine uses `check_same_thread=False` for the
threaded runtime. Models and shared state definitions live in `models.py`:
`LibraryFolder`, `Track`, `Job`, `FileChange`, `ScheduledTask`,
`IncompleteAlbum`, and `HealthItem`. Schema changes must account for both the
boot-time compatibility migration in `db._migrate` and the Alembic history.

`config.env()` contains deploy-time paths and credentials loaded from the
environment or Docker secrets. `config.settings()` contains validated,
user-editable preferences persisted atomically in `settings.json`. The two
layers serve different lifetimes and should not be merged.

## Ingest and review flow

Inputs arrive from the watchdog handler, web uploads, or bulk re-tag requests.
Those entry points create `Job` rows through `pipeline.enqueue` and submit job
IDs to one in-memory worker queue. Enqueueing serializes its active-job
deduplication so watcher and HTTP requests cannot create duplicate work.

For files grouped under one source album, `ingest.album` elects one release for
the group before per-file fallback. Ungrouped identification uses existing tag
clues, MusicBrainz text search, and optional AcoustID. Candidate scoring and
release selection happen before `pipeline.prepare_tags` normalizes required
fields and formatting.

Every automatic path converges on `pipeline._finalize_and_commit`. Dry runs and
low-confidence or incomplete matches become review jobs rather than writes.
`pipeline._commit_tag_path` is the shared safety choke point: it checks the
destination library for likely duplicates before artwork, lyrics, tag mutation,
or movement; then it snapshots tags, writes atomically, builds the destination,
moves the file, records `FileChange`, and upserts the `Track` index. Failures
that need a decision retain enough serialized candidate and tag data for the
queue to render without another MusicBrainz lookup.

Review routes in `main.py` support candidate, manual, bulk, conflict, and skip
flows. Accepted work is backgrounded with `tasks.run_chain`; explicit duplicate
application uses the existing review state as the deliberate second-apply
override. In-library single-track matching delegates to
`library.retag.apply_match`.

The ingest state progression is:

```text
queued -> identifying -> tagging -> moving -> done
                     \-> needs_review -> tagging/moving or skipped
                     \-> error
running -> done or error       (generic background jobs)
```

Use `models.ACTIVE_JOB_STATUSES` wherever in-flight work matters. Review jobs
are pending user decisions, but they are not background tasks currently doing
work.

## Background work and time

`tasks.run_task` and `tasks.run_chain` create tracked `Job` rows and daemon
threads with `TaskCtx` logging, progress, heartbeat, and cancellation. Live
thread registration prevents `tasks.reap_stale_jobs` from declaring a quiet but
still-running task dead. New long-running work belongs here, not in request
handlers or untracked threads.

`scheduler.start` runs a single periodic loop. `scheduler.TASK_TYPES` is the
user-visible registry and `run_task_by_type` dispatches scheduled work through
the task runner. Cron expressions use the same display-timezone resolution as
the UI, while stored timestamps remain naive UTC.

## Subsystem boundaries

- `identify/` reads clues and obtains candidates; it must not mutate files.
- `tagging/schema.py` defines conceptual metadata. `tagging/writers/` owns
  format-specific full writes; `tagging/partial.py` owns atomic field updates;
  `snapshot.py` powers reversion.
- `library/paths.py` calculates safe canonical destinations. `mover.py` performs
  verified movement. `scanner.py` indexes disk state. `organizer.py`,
  `actions.py`, `retag.py`, and `revert.py` perform user-requested operations.
- `tasks.py` owns generic execution state; `scheduler.py` decides when a known
  task type should run.
- `main.py` owns HTTP contracts and composition, not domain algorithms.

## Extension recipes

- **User setting:** add the validated field to `config.UserSettings`, the
  `settings.html` control, the `main.settings_update` form parameter and patch,
  and its consumer. Treat omitted checkboxes explicitly.
- **Tag field:** extend `TrackTags` and `to_vorbis`, implement all four format
  writers, and update normalized readback when the field is indexed or edited.
- **Library helper/report:** implement the established `(folder_id, ctx=None)`
  callable and register it in `library.actions.LIBRARY_ACTIONS`. The generic
  `main.library_actions_run` dispatcher queues selected registry entries; add a
  bespoke route only when the interaction cannot use that contract.
- **Long or scheduled task:** wrap work in `run_task`/`run_chain`. A new
  schedulable type also updates `TASK_TYPES`, `run_task_by_type`, schedule form
  validation, and the schedule template.
- **Identification source:** keep lookup/parsing in `identify/`, wire selection
  through the pipeline, and ensure every result still reaches
  `_finalize_and_commit`.
- **Route or template:** authenticate non-public routes, use POST for mutation,
  preserve plain-form fallbacks for HTMX flows, and add route-level tests for
  security or state changes.

For any file mutation or pipeline extension, read
[INVARIANTS.md](INVARIANTS.md) before implementation.
