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
