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
  config.py                Env vars · Docker secrets · settings.json layers
  db.py                    SQLite engine bootstrap + ad-hoc helpers (e.g. dashboard_stats)
  models.py                SQLModel tables: LibraryFolder · Track · Job + enums
  auth.py                  argon2 verify + session helpers
  notify.py                Discord webhook sender
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
    schema.py              TrackTags dataclass + Vorbis rendering
    formatter.py           Smart formatting (Title Case, qualifiers, grammar)
    partial.py             Single-field write helpers (lyrics, cover, advisory)
    coverart.py            Cover Art Archive fetcher
    lyrics_fetcher.py      LRCLIB client
    advisory.py            Explicit-content classifier
    writers/               Format dispatch: flac · mp3 · mp4 · wav
  library/
    paths.py               sanitize_segment + build_destination
    mover.py               Move with conflict detection + cover.jpg writer
    scanner.py             Index existing files into Track table (batches 50)
    organizer.py           Reorganize files; also prunes empty leftover dirs
    actions.py             Individual library actions (covers, replaygain, integrity, disc, missing)
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
```

Job rows carry `candidates_json`, `chosen_tags_json`, and `destination_path` so the review UI can render without re-querying MusicBrainz.

## Threading

- **One** worker thread (`pipeline.start_worker`) pulls from `queue.Queue`. SQLModel sessions are per-call; engine uses `check_same_thread=False`.
- One-shot daemon threads kick off long-running library operations (scan, organize, re-tag, individual actions).
- Webhook posts fire on their own daemon thread so they cannot block the pipeline.

## Where new code goes

- **A new tag field** → `tagging/schema.py` (TrackTags + `to_vorbis`) + every writer in `tagging/writers/`. Add to settings only if it needs configuration.
- **A new individual library action** → `library/actions.py` function + a route in `main.py` (look for the `/library/extract-covers` block as a template).
- **A new pipeline step** → `ingest/pipeline._process_inner`. Keep the function flat — the review-branch routing is at the bottom.
- **A new identifier source** → `identify/` with the same shape as `musicbrainz` / `acoustid`; wire in `pipeline`.
- **A new user-editable setting** → `config.UserSettings` field, `settings.html` form input (use the `tip()` macro), and the `/settings` POST handler in `main.py`.
