---
name: project-overview
description: One-page description of what dragontag is, its goals, and where the source of truth lives
metadata:
  type: project
---

# dragontag

Self-hosted, Docker-native music tagger and library organizer. Drop an audio file → it's identified against MusicBrainz (with AcoustID fallback), tagged with a full Vorbis-style schema, has cover art + lyrics embedded, and is moved into a clean `Album Artist / Album / NN. Title.ext` layout (grouped by primary album-artist, featured guests stripped).

## Goals

- **Hands-off for high-confidence matches.** Score-gated auto-apply keeps the user out of the loop unless something is genuinely ambiguous.
- **Browser-driven review** for everything else (low score, missing RELEASETYPE, destination conflict, dry-run preview).
- **Format-agnostic schema.** Same conceptual tags written across FLAC / MP3 / WAV / M4A.
- **Single-user, single-instance.** SQLite + threads, not Postgres + workers.

## Key surfaces

- `/` Dashboard · `/jobs` queue (ingests + background tasks with progress) · `/review` candidate picker · `/library` browse + actions · `/changes` tag-change history + revert/move-back · `/schedule` cron scheduling · `/settings` UI-editable config incl. backup/restore + log verbosity · `/docs` user manual · `/api-docs` + `/openapi.json` auth-guarded API reference · `/setup` first-run wizard · `/health` unauthenticated.
- Background: watchdog observer on `/drop`, one worker thread feeding an in-memory `queue.Queue`, a 30s cron-scheduler thread, and per-task daemon threads via `tasks.run_task`. A universal progress bar in `base.html` polls `GET /api/progress`.

## Source of truth

- Schema/conventions: [[conventions]] + `dragontag/app/tagging/schema.py`
- Config layering (env → secret files → settings.json): `dragontag/app/config.py`
- Tag rendering: `dragontag/app/tagging/writers/`
- Recent change history: `CHANGELOG.md` (current sweep at top; older work consolidated)
