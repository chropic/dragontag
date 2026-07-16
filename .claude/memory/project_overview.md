---
name: project-overview
description: One-page description of what dragontag is, its goals, its surfaces, and where the source of truth lives
metadata:
  type: project
---

# dragontag

Self-hosted, Docker-native music tagger and library organizer. Drop an audio file → it's
identified against MusicBrainz (with AcoustID fingerprint fallback), tagged with a full
Vorbis-style schema, has cover art (Cover Art Archive) + lyrics (LRCLIB) embedded, and is moved
into a clean `Album Artist / Album / [Disc N/] NN. Title.ext` layout (grouped by primary
album-artist, featured guests stripped from folder names).

## Goals — use these to resolve design questions

- **Hands-off for high-confidence matches.** Score-gated auto-apply (`score_threshold`, default
  0.85) keeps the user out of the loop unless something is genuinely ambiguous.
- **Browser-driven review** for everything else (low score, no match, missing RELEASETYPE,
  destination conflict, dry-run preview). The review queue is a first-class surface, not an
  error bin.
- **Format-agnostic schema.** The same conceptual tags are written across FLAC / MP3 / WAV / M4A.
  A field that exists in only some writers is a bug.
- **Single-user, single-instance.** SQLite + threads, not Postgres + workers. Do not add
  multi-tenant, horizontal-scale, or heavy-dependency machinery.
- **Never damage the user's audio files.** Atomic writes, verified moves, per-path locks, and
  loud DIVERGED reporting when automatic recovery is impossible. This goal outranks convenience.

## Key surfaces (all routes in `dragontag/app/main.py`)

- `/` Dashboard (counts, ASCII banner)
- `/queue` Jobs + Review in one page (old `/review` and `/jobs` 308-redirect here); per-job
  detail at `/jobs/{id}`
- `/library` browse (tabs per LibraryFolder, sortable/paginated track table), individual +
  helper/report actions, `/library/incomplete` (persisted missing-track results)
- `/changes` tag-change history — revert tags in place, or move a file back to its original dir
- `/schedule` cron scheduling of tasks (croniter; expressions read in the display timezone)
- `/settings` everything user-editable, backup/restore, log verbosity 0–4
- `/docs` in-app user manual (template `docs.html`) — FastAPI's own docs are disabled;
  auth-guarded Swagger lives at `/api-docs` + `/openapi.json`
- `/setup` first-run wizard · `/login` · `/health` (unauthenticated)

Background machinery: watchdog observer on `/drop` (size-settle window), **one** ingest worker
thread feeding an in-memory `queue.Queue`, a 30s cron-scheduler tick thread, and per-task daemon
threads via `tasks.run_task`. The universal progress bar in `base.html` polls `GET /api/progress`
every 3s.

## Storage

- SQLite at `${DRAGONTAG_CONFIG_PATH}/dragontag.db` (SQLModel; `check_same_thread=False`).
  Tables: `LibraryFolder`, `Track`, `Job`, `FileChange`, `ScheduledTask`, `IncompleteAlbum`.
- `settings.json` in the same dir (atomic writes via `config._Store`; layered env → Docker
  secrets → settings.json).
- All datetimes stored naive-UTC (`timeutil.now_utc()`).

## Source of truth

- Tag schema/conventions: [[conventions]] + `dragontag/app/tagging/schema.py`
- Config layering: `dragontag/app/config.py` (`env()` for deploy paths, `settings()` for user prefs)
- Tag rendering: `dragontag/app/tagging/writers/` (flac / mp3 / mp4 / wav + `_atomic`, `_id3common`)
- Destination paths: `dragontag/app/library/paths.py`
- Recent change history: `CHANGELOG.md` (current WIP section at top; older work consolidated —
  don't re-expand it)
- Known bug patterns: [[gotchas]] — two full bug sweeps (PR #39 tagging pipeline, PR #40
  core/library/web) are distilled there.
