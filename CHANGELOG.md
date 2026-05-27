<!-- AGENTS: When a task from TODO.md is completed, append a new entry to the bottom of this file following the same format. Do not edit existing entries. -->

## Task 0 — Rename project to dragontag, port to 7593
**Branch:** `task/0-rename-dragontag` → PR #1

- Renamed Python package directory `aio_tagger/` → `dragontag/`
- Replaced all `aio-tagger` / `aio_tagger` references with `dragontag` across source, config, Dockerfile, docker-compose, README, LICENSE, and tests
- Changed port from `8080` to `7593` in Dockerfile, docker-compose.yml, and README

---

## Task 1 — More tag fields and customization options
**Branch:** `task/1-more-tags` → PR #2

### New tag fields
Six new fields added to `TrackTags` and written to all supported formats (FLAC/MP3/WAV/MP4):

| Field | Source | FLAC key | ID3 frame | MP4 atom |
|---|---|---|---|---|
| `conductor` | MB recording artist-relations | `CONDUCTOR` | `TPE3` | freeform |
| `lyricist` | MB work artist-relations | `LYRICIST` | `TEXT` | freeform |
| `arranger` | MB work artist-relations | `ARRANGER` | `TXXX:ARRANGER` | freeform |
| `catalog_number` | MB label-info | `CATALOGNUMBER` | `TXXX:CATALOGNUMBER` | freeform |
| `language` | MB text-representation | `LANGUAGE` | `TLAN` | freeform |
| `compilation` | Derived from release-group type | `COMPILATION` | `TCMP` | `cpil` |

`acoustid_id` was already in the schema but never populated. Fixed by switching from the high-level `acoustid.match()` to `fingerprint_file()` + `lookup()`, which exposes the AcoustID UUID in the API response.

### New customization options (all in settings UI + persisted to settings.json)
- **`genre_limit`** — max genres written per track (default `3`, set `0` for no limit)
- **`genre_casing`** — `"title"` (default) / `"lower"` / `"as-is"` (raw MB tag strings)
- **`skip_fields`** — checklist of `TrackTags` attribute names; checked fields are suppressed at write time across all formats
- **All multi-value separators** now exposed in settings UI — `ARTISTSORT`, `ALBUMARTISTSORT`, `LABEL`, `ISRC`, `COMPOSER`, `CONDUCTOR`, `LYRICIST`, `ARRANGER` were previously hardcoded to `";"`

### Files changed
`dragontag/app/tagging/schema.py`, `dragontag/app/tagging/writers/_id3common.py`, `dragontag/app/tagging/writers/mp4.py`, `dragontag/app/tagging/writers/__init__.py`, `dragontag/app/config.py`, `dragontag/app/identify/acoustid.py`, `dragontag/app/identify/musicbrainz.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/main.py`, `dragontag/app/web/templates/settings.html`, `tests/test_schema_vorbis.py`

---

## Tasks 2, 7, 8 — Library DB, organize, bulk-retag
**Branch:** `task/2-7-8-library` → PR #3

### Task 2 — Library database
- New `LibraryFolder` model: tracks one or more on-disk library paths with label, enabled flag, and priority for routing
- New `Track` model: one row per audio file; stores title, artist, album, album_artist, track_num, disc_num, disc_total, duration, mb_track_id, mb_album_id, library_folder_id, path (unique), indexed_at, last_seen
- `Job` gains nullable `track_id` FK — set after a file successfully moves into the library
- DB seeds one `LibraryFolder` from `env().library_path` on first boot (idempotent)
- `build_destination()` gains optional `library_root` kwarg; all existing callers unaffected
- `existing_tags.read()` now returns `track`, `disc`, and `disc_total` keys
- Pipeline's `_commit_tag_path()` routes through `_pick_library_folder()` (lowest priority, then id) and upserts a `Track` row after every successful move
- `/library` browse page: folder tabs, HTMX live search, track table
- `/library/folders` CRUD: add/remove `LibraryFolder` records (does not delete files)

### Task 7 — Organize existing library files
- New `library/organizer.py`: iterates `Track` rows for a given folder, computes target path via `build_destination()`, moves if different, updates `Track.path` in DB; conflicts logged but do not abort
- `/library/organize` POST triggers organize in a daemon thread; redirects immediately

### Task 8 — Bulk re-tag
- New `ingest/bulk.py`: `enqueue_folder(path)` walks a source directory and enqueues every supported audio file through the full identify → tag → move pipeline
- New `library/scanner.py`: `scan_folder(path, folder_id)` indexes existing on-disk files into the `Track` table without re-running the pipeline (reads tags via `existing_tags.read()`, parses slash-form track/disc numbers)
- `/library/scan` POST triggers scan in a daemon thread; `/library/bulk-retag` POST enqueues synchronously then redirects to dashboard

### Files changed
`dragontag/app/models.py`, `dragontag/app/db.py`, `dragontag/app/identify/existing_tags.py`, `dragontag/app/library/paths.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/main.py`, `dragontag/app/web/templates/base.html` (nav link), new: `dragontag/app/library/scanner.py`, `dragontag/app/library/organizer.py`, `dragontag/app/ingest/bulk.py`, `dragontag/app/web/templates/library.html`, `dragontag/app/web/templates/_library_tracks.html`, `dragontag/app/web/templates/library_folders.html`, `TASKS.md`, `CHANGELOG.md`

---

## Tasks 3, 4 — Lyrics pipeline
**Branch:** `task/3-4-lyrics` → PR #4

### Task 4 — Lyrics fetching and embedding
- New `tagging/lyrics_fetcher.py`: LRCLIB API client using `requests` (no new dependency); tries `/api/get` (exact match by artist + title + album + duration), falls back to `/api/search`; prefers synced LRC format, falls back to plain text; silently returns `None` on any error so pipeline always continues
- `TrackTags` gains `lyrics: str | None` field
- Per-format embedding: FLAC gets `LYRICS` Vorbis comment (via `to_vorbis()` automatically); MP3/WAV get a `USLT` (unsynchronized lyrics text) ID3v2.4 frame; M4A/MP4 get the standard `©lyr` atom
- `lyrics_enabled: bool = True` toggle in `UserSettings` + settings UI checkbox

### Task 3 — Explicit auto-tagger
- New `tagging/advisory.py`: word-boundary regex classifier; word list and `strip_lrc_timestamps()` logic ported from `L:\Files\Repos\autoadvisory`; no external dependency
- `TrackTags` gains `advisory: int | None` field (0=clean, 1=explicit, None=no lyrics)
- Per-format embedding: FLAC gets `ITUNESADVISORY` Vorbis comment; MP3/WAV get `TXXX:ITUNESADVISORY`; M4A/MP4 get the `rtng` integer atom
- Both lyrics fetch and advisory classification run inside `_commit_tag_path()` — review-path jobs get lyrics automatically

### Files changed
`dragontag/app/tagging/schema.py`, `dragontag/app/config.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/main.py`, `dragontag/app/tagging/writers/_id3common.py`, `dragontag/app/tagging/writers/mp4.py`, `dragontag/app/web/templates/settings.html`, new: `dragontag/app/tagging/lyrics_fetcher.py`, `dragontag/app/tagging/advisory.py`, `tests/test_lyrics_advisory.py`

---

## Tasks 5, 6 — Per-job UX & review flow + Track.advisory DB column
**Branch:** `task/5-6-review-ux` → PR #5

### Task 6 — Per-job detail page & dry-run review flow
- `dry_run: bool = False` added to `UserSettings`; exposed as checkbox in settings UI
- Pipeline's `_process_inner` detects `settings().dry_run` and routes to `needs_review` with `ReviewReason.dry_run` immediately after `assemble_tags()`, storing chosen tags and computed destination without writing/moving
- `ReviewReason.dry_run` added to the enum
- Review `/review/{id}/apply` now accepts optional `cover_art_url` (URL) and `cover_art_file` (upload); bytes override is applied before `_commit_tag_path`
- Cover art guard added in `_commit_tag_path`: fetches from Cover Art Archive only when `tags.cover_bytes` is absent; user-supplied bytes written with `min_overwrite_pixels=0`

### Task 5 — Track.advisory persisted to DB
- `Track.advisory: int | None` column added to the `Track` SQLModel
- Idempotent migration in `db.py` (`ALTER TABLE track ADD COLUMN advisory INTEGER`) guarded by `PRAGMA table_info`
- `_upsert_track` now writes `advisory` on both insert and update paths

### Files changed
`dragontag/app/models.py`, `dragontag/app/db.py`, `dragontag/app/config.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/main.py`, `dragontag/app/web/templates/settings.html`

---

## Tasks 9, 14 — Docker security hardening + GitHub Actions CI/CD
**Branch:** `task/9-14-docker-ci` → PR #6

### Task 9 — Least-privilege container
- New system user `dragontag` (uid 1000) created in Dockerfile; `USER dragontag` set before `CMD`
- `docker-compose.yml` updated: `user: "1000:1000"`, `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`
- Image reference switched to `ghcr.io/chropic/dragontag:main`; `build: .` kept as comment for local dev
- Inline comment block documents the capability audit and the `chown 1000:1000` prerequisite for host volume dirs

### Task 14 — GitHub Actions CI/CD
- New `.github/workflows/ci.yml` with two jobs:
  - `test`: runs on every push + PR; `setup-python 3.12` → `pip install -e ".[dev]"` → `pytest -v`
  - `docker`: depends on `test`; skipped on PRs; logs into GHCR with `GITHUB_TOKEN`; uses `docker/metadata-action` for branch/semver/SHA tags; `docker/build-push-action` with GHA layer cache
- README: CI badge added; Quick Start updated to GHCR image + `chown` note; Roadmap items for PRs #1–#5 ticked

### Files changed
`Dockerfile`, `docker-compose.yml`, `README.md`, new: `.github/workflows/ci.yml`

---

## Tasks 10, 11 — First-run setup wizard + terminal24 theme
**Branch:** `task/10-11-theme-wizard` → PR #8

### Task 10 — First-run setup wizard
- `/setup` GET/POST routes: credentials + AcoustID key configuration on first boot
- `config.py` resolve functions fall back to wizard-written files in config dir
- Standalone `setup.html` template matching terminal24 theme

### Task 11 — terminal24 theme
- Full terminal24 visual overhaul across all templates
- IBM Plex Mono/Sans, CRT scanlines, `#0c0c0c` cards, zero border-radius
- Pastel status badges (green/red/amber on black)

### Files changed
`dragontag/app/config.py`, `dragontag/app/main.py`, all templates in `dragontag/app/web/templates/`, new: `dragontag/app/web/templates/setup.html`

---

## Pre-ship features
**Branch:** `task/pre-ship-features` → PR #10

- **Health check** — public `GET /health` returns `{"status": "ok"}` (no auth); Docker `HEALTHCHECK` stanza added to image
- **MusicBrainz retry** — `_mb_retry` wraps `search_recordings`, `fetch_release`, and `fetch_recording` with exponential backoff (2 × 2s) on `WebServiceError`
- **Session cookie max-age** — `SessionMiddleware` now sets a 7-day cookie lifetime
- **Re-queue jobs** — `POST /jobs/{id}/requeue` resets a `done`/`error`/`skipped` job and puts it back through the full pipeline; Re-queue button added to job detail page
- **Dashboard pagination** — `/` and `/jobs/table` accept `?page=N`; prev/next controls rendered in the jobs table partial
- **Dashboard log surface** — inline log toggle per job row; HTMX loads log text on demand from `GET /jobs/{id}/log`
- **Discord webhook notifications** — `post_done` / `post_error` fire-and-forget in daemon threads; `webhook_url`, `webhook_on_done`, `webhook_on_error` settings with UI section

### Files changed
`dragontag/app/main.py`, `dragontag/app/config.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/identify/musicbrainz.py`, `Dockerfile`, `dragontag/app/web/templates/dashboard.html`, `dragontag/app/web/templates/_jobs_table.html`, `dragontag/app/web/templates/job_detail.html`, `dragontag/app/web/templates/settings.html`, `README.md`, new: `dragontag/app/notify.py`

---

## Tasks 12, 13, 15, 16 — Polish & release
**Branch:** `task/polish-release`

- Scanner batches DB commits (50 files per transaction instead of per-file); session rollback on per-file errors preserves the rest of the batch
- Lazy imports for Pillow and requests (faster cold startup)
- DB indexes on `Job.updated_at` and `Track.library_folder_id`
- Event-driven watcher settle loop (no busy-poll when idle); skips settle sleep on idle timeouts
- Deduplicated filename uniquification utility in `library/paths.py`
- Type annotations added to pipeline internals
- Legacy `aio-*` thread names renamed to `dragontag-*`
- Scrubbed internal references (local paths, dev-only defaults)
- README updated: new feature bullets, corrected tag convention docs, cleaned roadmap
- Removed `TASKS.md` and `SESSION_HANDOFF.md`

---

## TODO 05.27.2026 — Infrastructure, UI & feature sweep
**Branch:** `claude/youthful-galileo-Xe7Ys`

### Infrastructure
- **Alembic migrations** — `alembic/` directory scaffolded with custom `env.py` reading `SQLModel.metadata` and the same dynamic DB URL as the app; `render_as_batch=True` for SQLite compatibility; `alembic>=1.13` added to project dependencies
- **Env var rename: `AIO_` → `DRAGONTAG_`** — all environment variables now use the `DRAGONTAG_` prefix; updated in `config.py`, `Dockerfile`, `docker-compose.yml`, `tests/conftest.py`, and `README.md`; migration note added to README

### Bug fixes
- **Duplicate queue** — `pipeline.enqueue()` now checks for an existing active job (queued/identifying/tagging/moving) at the same source path before creating a new one; prevents double-enqueue when upload handler and watcher both fire for the same drop-folder write
- **MusicBrainz `release-groups` include** — removed `"release-groups"` from `fetch_recording()` includes list; this string is not valid for `get_recording_by_id` and caused API errors; release-group data is already available via `rel.get("release-group")` from `fetch_release()` which correctly includes it
- **RELEASETYPE fallback** — when MB omits `release-group.type`, pipeline now infers it from track count: 1 → `Single`, 2–6 → `EP`, 7+ → `Album`; similarly defaults `release_status` to `"Official"` when absent; eliminates the most common "missing RELEASETYPE" review-queue trigger

### Processing
- **Upload validation** — `uploads.py` now validates extension against `{.flac, .mp3, .wav, .m4a, .mp4}`, checks MIME type, and rejects executables (`.sh`, `.py`, `.exe`, `.php`, etc.) and zero-byte files before writing to disk; returns HTTP 422 with descriptive error
- **Attribution tag** — new `TAGGER` field on `TrackTags` with value `tagged via dragontag/<version>` (read from `importlib.metadata`); written as Vorbis comment `TAGGER`, ID3 `TXXX:TAGGER`, and MP4 freeform atom `----:com.apple.iTunes:TAGGER`
- **Smart formatting** — new `tagging/formatter.py` module with `to_title_case()` (music-aware, preserves articles/prepositions), `fix_qualifiers()` (wraps bare "Live"/"Remix"/"Intro" at end of titles in parentheses), and `fix_grammar()` (collapses double spaces, trims trailing punctuation); applied in pipeline after tag assembly when `format_title_case` or `format_fix_qualifiers` settings are enabled
- **Partial tag write helpers** — new `tagging/partial.py` module with format-aware functions that update a single field without wiping existing tags: `write_lyrics()`, `write_advisory()`, `read_lyrics()`, `write_cover()`; used by the new individual library action routes

### UI / UX
- **Toast notification system** — Alpine.js `toastManager()` in `base.html`; HTMX responses fire `HX-Trigger: {"showToast": {...}}` headers; toasts auto-dismiss after 4 s; used for settings save, library actions, retag, webhook test
- **Active page indicator** — nav links receive `font-bold border-b border-white` when `active_page` context var matches; passed from every route
- **Taller header** — nav padding increased to `py-5 px-6`; brand link slightly larger
- **ASCII art banner** — monochrome `dragontag` ASCII art added to dashboard page
- **Favicon** — `static/favicon.svg` added; pixel-art "D" mark; linked in `<head>`
- **TZ-aware timestamps** — `_format_local()` helper converts UTC datetimes to the host timezone via `zoneinfo.ZoneInfo(os.environ.get("TZ", "UTC"))`; used for all job timestamps in `_jobs_table.html`
- **Dynamic page titles** — all templates set `{% block title %}dragontag | {Page}{% endblock %}`

### Library page
- **Terminology** — "Index folder" → "Scan folder"; "Bulk re-tag" → "Full library re-tag"
- **Dry run relocated** — `dry_run` checkbox removed from Settings; per-operation dry-run checkbox added directly to the "Full library re-tag" form on the Library page
- **Lyrics toggle relocated** — `lyrics_enabled` checkbox removed from Settings; inline toggle placed in the Library page individual-actions bar; submits to `/settings` via hidden-input form preserving all other setting values
- **Individual action routes** — three new POST routes: `/library/fetch-lyrics`, `/library/tag-advisories`, `/library/fetch-covers`; each iterates tracks in the selected folder and updates only the relevant tag field using `partial.py` helpers without re-running the full pipeline
- **Granular re-tag controls** — track table now has per-row checkboxes and a "Select all" toggle; new `POST /library/retag-selected` route accepts a list of track IDs and enqueues each through the full pipeline

### Settings page
- **Centered layout** — settings form wrapped in `max-w-2xl mx-auto`
- **Threshold tooltip** — `title="..."` attribute on the auto-apply threshold label explains the tradeoff
- **Smart formatting section** — two new checkboxes (`format_title_case`, `format_fix_qualifiers`) persisted to `UserSettings` and `settings.json`
- **Token palette + live preview** — filename template inputs show clickable token chips that insert `{token}` into the field; a live preview updates as you type using JS substitution with example values
- **Save confirmation** — settings POST redirects with `?saved=1`; GET handler returns `HX-Trigger` toast; inline "✓ Saved" indicator also shown
- **Webhook test button** — `POST /settings/test-webhook` fires a dummy payload to the configured webhook URL and returns a toast response

### New pages
- **Jobs page (`/jobs`)** — dedicated full queue view with stats bar (pending / done today / errors); bulk controls: cancel all queued, clear completed, clear errors; per-row cancel/requeue actions; linked from nav and dashboard
- **Docs page (`/docs`)** — in-app documentation with sections for pipeline overview, configuration reference (all `DRAGONTAG_*` vars), file formats, template tokens, review queue reasons, and webhook setup; linked from nav

### Dashboard
- **Condensed job list** — shows last 10 jobs only; "View all →" link to `/jobs`
- **Library stats panel** — aggregate query returns total tracks, albums, artists; displayed in a stats grid
- **Folder tagger** — path input + "Enqueue all" button calls existing `bulk.enqueue_folder()` flow

### Files changed
New: `alembic/env.py`, `alembic/versions/` (empty), `alembic.ini`, `dragontag/app/tagging/formatter.py`, `dragontag/app/tagging/partial.py`, `dragontag/app/web/static/favicon.svg`, `dragontag/app/web/templates/jobs.html`, `dragontag/app/web/templates/docs.html`

Modified: `dragontag/app/config.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/ingest/uploads.py`, `dragontag/app/identify/musicbrainz.py`, `dragontag/app/tagging/schema.py`, `dragontag/app/tagging/writers/_id3common.py`, `dragontag/app/tagging/writers/mp4.py`, `dragontag/app/main.py`, `dragontag/app/web/templates/base.html`, `dragontag/app/web/templates/dashboard.html`, `dragontag/app/web/templates/settings.html`, `dragontag/app/web/templates/library.html`, `dragontag/app/web/templates/_library_tracks.html`, `dragontag/app/web/templates/_jobs_table.html`, `Dockerfile`, `docker-compose.yml`, `tests/conftest.py`, `pyproject.toml`, `README.md`
