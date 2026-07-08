---
name: architecture
description: Module layout, request/job flow, background workers, locking model, data-integrity invariants, and where to put new code
metadata:
  type: project
---

# Architecture

## Stack

- **FastAPI** + **Jinja2** (autoescape on via Starlette `Jinja2Templates`) + **HTMX** +
  **Alpine.js** + **Tailwind** — all assets self-hosted, no CDN. The stylesheet is compiled ahead
  of time: `frontend/app.input.css` + `frontend/tailwind.config.js` → committed
  `dragontag/app/web/static/app.css` via `bash frontend/build_css.sh`. **Rerun it after
  adding/removing utility classes in templates** or the new classes silently do nothing.
- **UI is a terminal/TUI design (Direction A)** — JetBrains Mono (vendored woff2, falls back to
  vendored IBM Plex Mono), true-black canvas, `.dt-*` texture primitives
  (panel/label/statusbar/cursor/meter) defined in `app.input.css` `@layer components`, bracketed
  `[ label ]` buttons, text+glyph status (`● done · ▲ review · ✕ error`), a `fixed` keybind
  status bar (`{% block statusbar %}` in `base.html`). Green is reserved for *meaning*
  (done/active/focus/progress); amber = review, red = error.
- **SQLModel / SQLite** at `${DRAGONTAG_CONFIG_PATH}/dragontag.db`. **Alembic** scaffolded under
  `alembic/` (`render_as_batch=True` for SQLite ALTER); plus a pragmatic `db._migrate` that runs
  idempotent `ALTER TABLE ... ADD COLUMN` statements at boot — **one transaction per ALTER** so a
  duplicate-column failure can't skip the rest.
- **mutagen** for tag I/O; **musicbrainzngs** + **pyacoustid** (`fpcalc` binary, bundled in the
  Docker image) for identification. Python ≥ 3.12 required.

## Layout (annotated)

```
dragontag/app/
  main.py                  ALL FastAPI routes (large — search by route path or function name).
                           Also: _local_tz()/_format_local() display-timezone helpers,
                           _toast_response(), _read_upload_capped(), startup wiring.
  config.py                Env vars · Docker secrets · settings.json layers. env() = deploy paths
                           (frozen), settings() = UserSettings (user-editable, validated),
                           store().transact(patch_fn) = atomic read-modify-write. reset_store()
                           exists for backup restore.
  db.py                    Engine bootstrap (double-checked lock), _migrate (ad-hoc ALTERs),
                           _seed_library_folder, session() helper, reset_engine() for restore.
  models.py                SQLModel tables: LibraryFolder · Track · Job · FileChange ·
                           ScheduledTask · IncompleteAlbum + JobStatus enum +
                           ACTIVE_JOB_STATUSES + append_job_log (byte-capped, 256 KiB).
  auth.py                  argon2 verify + signed-cookie session helpers + require_auth dep.
  notify.py                Discord webhook sender. ENTIRE body is try/excepted — the
                           "errors are logged, never raised" contract includes payload build.
  tasks.py                 Background task runner: run_task(kind, name, fn) → Job row + daemon
                           thread + TaskCtx (.log/.progress heartbeat → updated_at, ~1s throttle).
                           run_chain for multi-step. reap_stale_jobs: 15-min heartbeat reaper
                           that SKIPS jobs whose worker thread is still alive (_live_threads).
                           request_cancel + _cancel_events drive the Stop button.
  scheduler.py             croniter scheduler, 30s tick daemon. TASK_TYPES dispatch table;
                           run_task_by_type. Cron expressions are interpreted in _cron_tz()
                           (TZ env → settings().timezone → UTC) and next-fire times converted
                           to naive UTC for storage/compare. _tick calls reap_stale_jobs first.
  backup.py                Versioned tarball (manifest + sha256, sqlite backup API) + validated
                           restore (refuses while jobs active; cross-filesystem-safe staging).
  logsetup.py              0–4 verbosity → logging levels; tolerant of corrupt values (→ INFO).
  timeutil.py              now_utc() — naive-UTC everywhere in the DB.
  ingest/
    pipeline.py            Per-file orchestration. enqueue (dedup under _enqueue_lock) → process
                           → _process_inner (identify branches) → _finalize_and_commit →
                           _commit_tag_path (snapshot → write_tags → move, under path_lock) →
                           _record_change/_upsert_track. start_worker/_worker_loop (ONE thread),
                           submit, resubmit_pending (boot recovery).
    watcher.py             watchdog observer; _Handler._pending stores (ts, size) — a path is
                           released only when the settle window elapsed AND size stopped changing.
    uploads.py             UI upload handler; streams 1 MiB chunks; unlinks the partial file if
                           the stream fails mid-write (drop folder must never see truncated files).
    bulk.py                Folder-level bulk re-tag enqueuer.
  identify/
    existing_tags.py       mutagen-based normalized tag reader; degrades to {"duration": None}
                           on unreadable headers. Knows TXXX:/UFID:/MP4-freeform MBID aliases.
    filename_parse.py      "Artist - Title" / "NN - Title" heuristics.
    musicbrainz.py         search_candidates (progressive fallback) + assemble_tags. _mb_retry
                           wrapper; _credit_names/_sorts/_ids/_phrase all guard malformed
                           credits with (c.get("artist") or {}).
    acoustid.py            fpcalc + AcoustID lookup; swallows ALL exceptions → [].
    scoring.py             Confidence model; weights sum to 1.0 by design (missing album/duration
                           caps the max score below auto-apply — intentional).
    genres.py              Whitelist filter (vendored data/genres.txt) + junk fallback.
  tagging/
    schema.py              TrackTags dataclass + to_vorbis(sep) → dict[str, str | list[str]]
                           (native multi-value). Totals of 0 mean "unknown" and are NOT written.
    formatter.py           Smart formatting (Title Case, qualifier parens, grammar fixes).
    partial.py             Single-field write helpers (lyrics, cover, advisory, basic tags,
                           write_album_link_tags) — all through atomic_inplace.
    snapshot.py            capture/restore a file's text tags (powers revert). Handles MP4
                           bool/int atoms explicitly. No embedded art / binary frames.
    coverart.py            Cover Art Archive fetcher (release; release-group behind setting).
    lyrics_fetcher.py      LRCLIB client.
    advisory.py            Explicit-content classifier. is_explicit(None) would crash —
                           all callers guard with `if fetched is not None` first; keep it that way.
    writers/               Format dispatch: flac · mp3 · mp4 · wav.
      _atomic.py           atomic_inplace(path): copy2 → temp (.dgtag-*) → mutate → fsync →
                           os.replace → fsync dir. Orphan sweeper for leftover temps.
      _id3common.py        Shared ID3v2.4 frame builder (TXXX_FIELDS, dedicated TSOP/TSO2, UFID).
  library/
    filelock.py            path_lock(path) — per-resolved-path threading.Lock. See "Locking".
    paths.py               sanitize_segment, primary_artist, build_destination, unique_path.
    mover.py               move(src, dst, overwrite=False) → MoveResult(moved, destination,
                           conflict). DOES NOT RAISE on conflict. Verifies byte count after move.
                           move_lyric_sidecar, write_cover_jpg (temp + os.replace).
    scanner.py             Index on-disk files into Track (batches of 50).
    organizer.py           organize_folder: recompute canonical path per track, move under
                           path_lock, update Track.path, roll back on DB failure (checking
                           MoveResult), report DIVERGED loudly when rollback impossible.
                           _prune_empty_dirs (bottom-up, never the library root).
    actions.py             LIBRARY_ACTIONS registry: key → (label, description, fn(folder_id,
                           ctx=None) -> dict). Keys map to routes /library/<key-with-dashes>.
                           BATCH_ORGANIZE / BATCH_RETAG step lists; build_chain_steps.
    filters.py             is_path_excluded(p, patterns, dirs) — applied by scanner, bulk, watcher.
    revert.py              revert_change (restore tags in place under path_lock) + move_back
                           (return file to original dir; rollback checks MoveResult; adds dest
                           to settings().scan_exclude_files so it isn't re-ingested).
  web/
    templates/             Jinja2, extend base.html. Fragments prefixed "_".
    static/                app.css (BUILT — don't hand-edit), favicon.svg, fonts/, vendor/.
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

- Job rows carry `candidates_json`, `chosen_tags_json`, `destination_path` so the review UI
  renders without re-querying MusicBrainz.
- `Job.kind` distinguishes pipeline ingests (`"ingest"`) from background tasks (`scan`,
  `organize`, `fetch_lyrics`, `fetch_covers`, `bulk_retag`, `backup`, per-action kinds,
  `library_chain`, `batch_organize/retag/nuclear`). Non-ingest jobs use `running`, carry
  `progress_current/total/item`, can't be requeued, and are marked `error("interrupted by
  restart")` by `resubmit_pending` instead of resubmitted.
- `Job.dry_run_override` (None = follow global `settings().dry_run`) carries per-run dry-run
  choices from the Library checkboxes; those never mutate the global setting.
- Use `models.ACTIVE_JOB_STATUSES` for "in-flight" checks — don't hand-roll status lists.
- `Job.log` is capped at 256 KiB **measured in encoded bytes** via `append_job_log`.

## Locking model — read this before moving or tagging any file

`library/filelock.path_lock(path)` is a per-resolved-absolute-path `threading.Lock`. Any
read-then-write on a file's tags or location must hold it. Current holders:

1. **Ingest worker** — `pipeline._commit_tag_path` (snapshot → write_tags → move).
2. **Revert / move-back** — `library/revert.py` (both directions, including the rollback move).
3. **Organizer** — `library/organizer.organize_folder` (move + Track.path update + rollback).

If you add a fourth mutator (a new action that renames/moves/retags), take the lock. The dict of
locks grows unbounded by design (single-user, bounded library) — do not "fix" that.

Related invariant: `mover.move(..., overwrite=False)` **returns** `MoveResult(moved=False,
conflict=True)` on a conflict instead of raising. Every caller must branch on `.moved`/
`.conflict`; two separate shipped bugs came from assuming it raises (see [[gotchas]]).

## Threading

- **One** ingest worker thread (`pipeline.start_worker`) pulls from `queue.Queue`. Sessions are
  per-call; engine uses `check_same_thread=False`.
- Long-running library operations go through `tasks.run_task(kind, name, fn)` → tracked Job +
  daemon thread registered in `tasks._live_threads`.
- **Stale-job reaper**: `tasks.reap_stale_jobs()` (called each scheduler tick) errors any
  `running` Job whose `updated_at` heartbeat is >15 min old **unless its worker thread is still
  alive** — a task in one long non-heartbeating step (e.g. zipping a big backup) is legitimate.
  Healthy tasks heartbeat via `TaskCtx.progress/.log` (bump `updated_at`, ~1/s throttle).
  This complements `resubmit_pending` (boot-time recovery only).
- `scheduler.start()` runs one 30s tick daemon. Webhook posts fire on their own daemon thread.
- `pipeline.enqueue` serializes its check-then-insert under `_enqueue_lock` (watcher/HTTP/bulk
  threads race otherwise).

## Time / timezone model

- **Storage & comparison**: naive UTC everywhere (`timeutil.now_utc()`).
- **Display**: `main._local_tz()` — Docker `TZ` env (locked, always wins) → in-app
  `settings().timezone` → UTC. `_format_local` renders.
- **Cron interpretation**: `scheduler._cron_tz()` uses the same resolution; `next_run` builds the
  croniter base in that tz and converts the next fire back to naive UTC. `describe_cron` output
  ("At 06:00 AM") therefore matches when it actually fires.
- Never call `datetime.utcnow()` (deprecated) or store tz-aware datetimes.

## Resilience / data-integrity invariants (all have regression tests)

- **Atomic tag writes**: every in-place mutagen save goes through
  `writers/_atomic.atomic_inplace(path)`. Any new audio-mutating code must use it.
  FLAC/MP4 clear tags in memory (`tags.clear()`), not `delete()` (avoids extra disk write).
- **Verified moves**: `mover.move` compares source byte count to destination after `shutil.move`;
  `os.path.samefile` is wrapped against a vanished source.
- **Rollback honesty**: organizer/move-back DB-failure rollbacks check `MoveResult.moved`; if the
  file can't be returned, they log CRITICAL "DIVERGED" and say so in the UI message instead of
  claiming success.
- **Network timeouts**: `settings().network_timeout_seconds` (default 15s) set as urllib socket
  default in `musicbrainz._ensure_configured` and passed to `acoustid.lookup`; a half-open
  connection can't wedge the single worker.
- **Watcher size-stability**: partial SMB/NFS transfers aren't ingested (settle window + stable
  size). Upload failures unlink the partial file for the same reason.
- **Defensive MB parsing**: all four credit helpers tolerate `"artist": null` / missing keys;
  `existing_tags.read` degrades instead of raising; `scoring._sim` NFC-normalizes + casefolds.
- **Zero totals are "unknown"**: `TrackTags.to_vorbis` and the MP4 writer skip
  `TRACKTOTAL`/`DISCTOTAL` when the value is 0.

## Where new code goes (recipes)

- **New tag field** → `tagging/schema.py` (TrackTags + `to_vorbis`) + all four writers.
  Multi-value fields emit native lists, never separator-joined strings.
- **New individual library action** → function `(folder_id, ctx=None) -> dict` in
  `library/actions.py`, register in `LIBRARY_ACTIONS` (key → (label, description, fn)); the
  Library page, multi-select chains, batches (`BATCH_ORGANIZE`/`BATCH_RETAG`) and scheduler all
  read the registry. Route appears at `/library/<key-with-dashes>`.
- **New pipeline step** → `ingest/pipeline._process_inner` (keep flat; review-branch routing at
  the bottom) or `_finalize_and_commit` for post-identify steps shared with the MBID short-circuit.
- **New identifier source** → `identify/` with the same shape as `musicbrainz`/`acoustid`;
  wire in `pipeline`.
- **New user-editable setting** → 4 places: `config.UserSettings` field (+ validator if it can
  break the pipeline), `settings.html` input (+ `hint(text)` line), `main.py::settings_update`
  Form param + patch dict, and the consumer. The `timezone` field is the worked example.
- **New long-running operation** → `tasks.run_task` (or `run_chain` for multi-step); accept
  optional `ctx` for `.log()`/`.progress()`. Never a bare daemon thread for new work.
- **New schedulable task type** → key in `scheduler.TASK_TYPES` + branch in `run_task_by_type`
  + params handling in `main.py::schedule_create` + `schedule.html`.
- **Global keyboard shortcut** → `dtKeys.register(key, fn)` (defined in `base.html`); Alt/Ctrl/
  Meta combos need their own `keydown` listener (dtKeys ignores modified keys). Every key shown
  in a page's statusbar must be wired.

## API surface notes

FastAPI's built-in docs stay disabled on the app object (the user manual owns `GET /docs`);
auth-guarded equivalents are hand-rolled `GET /openapi.json` and `GET /api-docs` in `main.py`.
`/health` is the only unauthenticated route besides login/setup/static.
