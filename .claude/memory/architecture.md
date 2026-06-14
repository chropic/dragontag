---
name: architecture
description: Module layout, request/job flow, background workers, and where to put new code
metadata:
  type: project
---

# Architecture

## Stack

- **FastAPI** + **Jinja2** + **HTMX** + **Alpine.js** + **Tailwind via CDN** (no build step).
- **SQLModel / SQLite** at `${DRAGONTAG_CONFIG_PATH}/dragontag.db`. **Alembic** scaffolded under `alembic/`.
- **mutagen** for tag I/O; **musicbrainzngs** + **acoustid** (`fpcalc`) for identification.

## Layout

```
dragontag/app/
  main.py                  FastAPI routes (everything route-shaped)
  config.py                Env vars · Docker secrets · settings.json layers (+ reset_store for restore)
  db.py                    SQLite engine bootstrap + ad-hoc helpers (+ reset_engine for restore)
  models.py                SQLModel tables: LibraryFolder · Track · Job · FileChange · ScheduledTask + enums + ACTIVE_JOB_STATUSES
  auth.py                  argon2 verify + session helpers
  notify.py                Discord webhook sender
  tasks.py                 Background task runner — run_task(kind, name, fn) creates a Job row
                           (kind != "ingest", status=running) with TaskCtx.log/.progress
  scheduler.py             Cron scheduler (croniter, 30s tick daemon) dispatching ScheduledTask
                           rows through tasks.run_task; TASK_TYPES is the dispatch table
  backup.py                Versioned backup tarball (manifest + sha256, sqlite backup API) +
                           validated restore (refuses while jobs active)
  logsetup.py              0–4 verbosity → logging levels; applied at startup + settings save
  ingest/
    pipeline.py            Per-file orchestration + background worker queue
    watcher.py             watchdog observer with settle window
    uploads.py             UI upload handler
    bulk.py                Folder-level bulk re-tag enqueuer
  identify/
    existing_tags.py       mutagen-based normalized tag reader
    filename_parse.py      "Artist - Title" / "NN - Title" heuristics
    musicbrainz.py         search + TrackTags assembler (has _mb_retry)
    acoustid.py            fpcalc + AcoustID lookup
    scoring.py             Confidence model
  tagging/
    schema.py              TrackTags dataclass + Vorbis rendering (native multi-value)
    formatter.py           Smart formatting (Title Case, qualifiers, grammar)
    partial.py             Single-field write helpers (lyrics, cover, advisory)
    snapshot.py            Capture/restore a file's tags (powers revert)
    coverart.py            Cover Art Archive fetcher
    lyrics_fetcher.py      LRCLIB client
    advisory.py            Explicit-content classifier
    writers/               Format dispatch: flac · mp3 · mp4 · wav (+ _atomic.py)
  library/
    paths.py               sanitize_segment + build_destination
    mover.py               Move with conflict detection + cover.jpg writer
    scanner.py             Index existing files into Track table (batches 50)
    organizer.py           Reorganize files; also prunes empty leftover dirs
    actions.py             Individual library actions (covers, replaygain, integrity, disc, missing)
    filters.py             is_path_excluded(p, patterns, dirs) — applied by scanner, bulk, watcher
    revert.py              Undo a recorded FileChange (restore tags in place)
  web/
    templates/             Jinja2 (extends base.html)
    static/                favicon, eventual static assets
```

## Job state machine (`models.JobStatus`)

```
queued → identifying → tagging → moving → done
                  ↘ needs_review (low score / no match / conflict / dry run)
                  ↘ error
needs_review → tagging → moving (after user resolves)
needs_review → skipped
running → done | error        (background tasks via tasks.run_task only)
```

Job rows carry `candidates_json`, `chosen_tags_json`, and `destination_path` so the review UI can render without re-querying MusicBrainz. `Job.kind` distinguishes pipeline ingests (`"ingest"`) from background tasks (`scan`, `organize`, `fetch_lyrics`, `fetch_covers`, `bulk_retag`, `backup`, the per-action kinds, `library_chain`, `batch_organize/retag/nuclear`); non-ingest jobs use `running`, carry `progress_current/progress_total/progress_item`, can't be requeued, and are marked `error("interrupted by restart")` by `resubmit_pending` instead of resubmitted. `Job.dry_run_override` (None = follow global `settings().dry_run`) carries the per-run dry-run choice from the Library checkboxes — those never mutate the global setting. The shared "in-flight" status set is `models.ACTIVE_JOB_STATUSES` — use it instead of hand-rolling status lists.

## Change history / revert

`pipeline._commit_tag_path` snapshots the file's existing tags (`tagging/snapshot.capture`) just before the destructive `write_tags`, then on `done` writes a `FileChange` row (original tags, original path, the written tags, whether it created `cover.jpg`). The `/changes` page lists recent rows; `library/revert.revert_change` rewrites the original tags **in place** (`snapshot.restore`) and removes a dragontag-created `cover.jpg`. `library/revert.move_back` returns the file to `FileChange.original_path` and appends the destination to `settings().scan_exempt_paths` (honored by watcher, scanner, and bulk re-tag) so it isn't re-ingested. Both repair the originating Job's `source_path`/`destination_path` (`_repair_job`) so a requeue afterwards works. History is pruned to `settings().max_recent_changes` (default 500, 0 = unlimited). Limitation: snapshots cover text tags only (no embedded art / exotic binary frames).

## Threading

- **One** worker thread (`pipeline.start_worker`) pulls from `queue.Queue`. SQLModel sessions are per-call; engine uses `check_same_thread=False`.
- Long-running library operations (scan, organize, lyrics/cover fetches, scheduled runs) go through `tasks.run_task`, which wraps a daemon thread with a tracked Job row. A few legacy actions (extract-covers, replaygain, integrity, disc-folders, missing-tracks) still use bare daemon threads.
- `scheduler.start()` runs one daemon tick-thread (30s); the universal progress bar in `base.html` polls `GET /api/progress` every 3s.
- Webhook posts fire on their own daemon thread so they cannot block the pipeline.
- **Stale-job reaper**: `tasks.reap_stale_jobs()` marks any `running` Job whose `updated_at` heartbeat hasn't advanced for `tasks.STALE_RUNNING_AFTER` (15min) as `error`; `scheduler._tick` calls it every tick. Healthy long tasks heartbeat via `TaskCtx.progress/.log` (which bump `updated_at`), so only hung/silently-dead tasks trip it. This is the in-process complement to `resubmit_pending` (which only runs at boot).

## Resilience / data-integrity invariants

- **Atomic tag writes**: every in-place mutagen save goes through `tagging/writers/_atomic.atomic_inplace(path)` — a `shutil.copy2` to a same-dir temp, mutate the temp, then `os.replace` back (atomic within one filesystem). Covers the full writers (`writers/*.py`), single-field updates (`tagging/partial.py`), and revert (`tagging/snapshot._restore_*`). A crash mid-save can only damage the temp, never the original. **Any new code that mutates an audio file must use this helper.** FLAC/MP4 clear tags in memory (`tags.clear()`) rather than `delete()` to avoid a redundant on-disk write.
- **Network timeout**: `settings().network_timeout_seconds` (default 15s) is applied as the urllib socket default in `musicbrainz._ensure_configured` and as the `acoustid.lookup(timeout=...)` arg, so a half-open connection can't hang the single ingest worker (which would also wedge `scheduler`'s same-kind check). `acoustid.lookup` swallows *all* exceptions → `[]`.
- **Move verification**: `library/mover.move` captures the source size and asserts the destination matches after `shutil.move`; `os.path.samefile` is wrapped against a vanished source. `write_cover_jpg` is also temp+`os.replace`.
- **Watcher size-stability**: `_Handler._pending` stores `(ts, size)`; `_collect_ready` only releases a path once the settle window elapsed *and* its size stopped changing (guards partial SMB/NFS transfers). Extracted from `settle_loop` for unit-testing.
- **Enqueue dedup**: `pipeline.enqueue` serializes its check-then-insert under `_enqueue_lock` so concurrent watcher/HTTP/bulk threads can't create duplicate jobs for one path.
- **Defensive parsing**: `musicbrainz.assemble_tags` uses `_credit_names/_credit_sorts/_credit_ids` (tolerant of malformed MB artist-credits); `existing_tags.read` degrades to `{"duration": None}` on an unreadable header; `scoring._sim` NFC-normalizes + casefolds before comparing.

## API surface notes

FastAPI's built-in docs stay disabled on the app object (the user manual owns `GET /docs`); auth-guarded equivalents are hand-rolled routes `GET /openapi.json` and `GET /api-docs` in main.py.

## Where new code goes

- **A new tag field** → `tagging/schema.py` (TrackTags + `to_vorbis`) + every writer in `tagging/writers/`. Add to settings only if it needs configuration. Note: `to_vorbis` returns `dict[str, str | list[str]]` — multi-value fields are emitted as **native lists** (one value each), not separator-joined strings.
- **A new individual library action** → `library/actions.py` function + a route in `main.py` (look for the `/library/extract-covers` block as a template).
- **A new pipeline step** → `ingest/pipeline._process_inner`. Keep the function flat — the review-branch routing is at the bottom.
- **A new identifier source** → `identify/` with the same shape as `musicbrainz` / `acoustid`; wire in `pipeline`.
- **A new user-editable setting** → `config.UserSettings` field, `settings.html` form input (use the `tip()` macro), and the `/settings` POST handler in `main.py`.
- **Scan filters** — `UserSettings.scan_filter_patterns` (regex list, matched against filenames) and `scan_exclude_dirs` (absolute paths, `!` prefix stripped on save) are applied by `library/filters.py::is_path_excluded()` in scanner, bulk, and watcher. Settings → Scan filters card has two textareas.
- **A new long-running/background operation** → run it via `tasks.run_task(kind, name, fn)` so it shows on the Queue page and feeds the progress bar; accept an optional `ctx` (`TaskCtx`) in the underlying function for `.log()`/`.progress(current, total, item=...)`. Multi-step work → `tasks.run_chain(kind, name, [(label, fn), ...])` (one Job, `[i/n]`-prefixed logs, continues past failed steps).
- **A new individual library action** → implement `(folder_id, ctx=None) -> dict` in `library/actions.py`, register it in `LIBRARY_ACTIONS` (key → (label, description, fn); key maps to route `/library/<key-with-dashes>`), and add it to `BATCH_ORGANIZE` or `BATCH_RETAG` if it belongs in a batch. The Library page buttons, multi-select chains, batches and scheduler all read the registry.
- **UI note** → Review + Jobs are one page at `/queue` (template `queue.html`); old `/review` and bare `/jobs` 308-redirect there. Genres from MB are filtered through `identify/genres.py` (vendored whitelist + junk fallback, toggle `genre_whitelist_enabled`). `IncompleteAlbum` rows (written by find_missing_tracks) feed `/library/incomplete`.
- **A new schedulable task type** → add a key to `scheduler.TASK_TYPES` + a dispatch branch in `scheduler.run_task_by_type`, plus any params handling in `main.py::schedule_create` and `schedule.html`.
