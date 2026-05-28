<!-- AGENTS: Group new work under the current WIP heading. Historical sections below are summarized — do not expand them. -->

# Changelog

## Unreleased — TODO 05.27.2026 sweep
**Branch:** `task/todo-2026-05-27`

### Added
- Grammar correction filter under Smart formatting: lowercases ALL-CAPS, inserts apostrophes into contractions (DONT → don't) and possessives (PEOPLES X → people's X), normalizes punctuation spacing. Toggle: `format_grammar_correct`.
- Dashboard library-stats panel: top artists, explicit count, lyrics count, average length.
- Per-setting hover tooltips on every option in `/settings`.
- Jobs page bulk buttons: **Clear all** (non-active rows) and **Clear needs_review**.
- Review page bulk selector: per-row checkboxes + "Apply top candidate" multi-action.
- Library data table: column sorting (title, artist, album, disc, duration, path, track) and standard pagination with selectable page sizes (10/25/50/100/200) replacing infinite scroll.
- Individual library actions: **Extract embedded covers**, **Recompute ReplayGain** (rsgain/loudgain), **Verify file integrity**, **Fix disc folders**, **Find missing tracks**.
- Tooltips on existing "Dry run" and "Tag advisories only" actions.
- `db.dashboard_stats()` helper used by the dashboard route.
- `library/actions.py` module hosting the new individual actions.

### Changed
- Organize-library now removes empty leftover directories (only directories with zero contents — files are never deleted; library root preserved). Confirmation prompt added to UI.
- `/docs` rewritten as a user manual with section navigation; environment variables, template tokens, review reasons, and webhook payload retained as the appendix.
- UI terminology: "Folder tabs / Scan folder / Organize folder / Individual actions for this folder" → "Library tabs / Scan library / Organize library / Individual library actions".
- Dashboard recent-jobs block condensed to 5 one-line rows.
- ASCII art on dashboard centered, brightened, denser.
- Review listing rows compacted (smaller vertical padding).
- Library track table: path cell properly truncates within bounding box via `table-fixed` + `truncate`.

### Files changed
Modified: `dragontag/app/config.py`, `dragontag/app/db.py`, `dragontag/app/main.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/library/organizer.py`, `dragontag/app/tagging/formatter.py`, `dragontag/app/web/templates/*.html`, `README.md`, `CHANGELOG.md`.
New: `dragontag/app/library/actions.py`, `tests/test_grammar.py`, `tests/test_organize_cleanup.py`.

---

## Previously shipped (consolidated)

History prior to this sweep is grouped semver-style. Each bullet represents work that previously had a dedicated task heading.

### Added
- Self-hosted FastAPI + HTMX UI; MusicBrainz-first identification with AcoustID fallback; confidence-scored auto-apply; review queue with candidate picker, cover art picker, RELEASETYPE override, and custom cover upload.
- Format coverage: FLAC (Vorbis), MP3 / WAV (ID3v2.4 + TXXX), M4A / MP4 (atoms + `----:com.apple.iTunes:NAME` freeform).
- Tag schema fields beyond the basics: `conductor`, `lyricist`, `arranger`, `catalog_number`, `language`, `compilation`, `acoustid_id`, lyrics (`lyrics_enabled`), advisory (`ITUNESADVISORY`), tagger attribution (`TAGGER`).
- Library subsystem: `LibraryFolder` + `Track` models, scanner (`scan_folder`), organizer (`organize_folder`), bulk re-tag (`enqueue_folder`), individual actions (fetch lyrics, tag advisories, fetch cover art, re-tag selected).
- Settings UI: AcoustID toggle, score threshold, filename templates with token palette + live preview, genre cap/casing, skip-field checklist, per-tag separators, watcher toggle, dry-run, webhook URL with Test button, smart-formatting toggles.
- Pages: Dashboard, Jobs (full queue + bulk controls), Review, Library, Library Folders, Settings, Docs, Login, Setup wizard (`/setup`).
- Webhook notifications (Discord-compatible) for done / error.
- Toast notifications (Alpine.js) driven by `HX-Trigger` headers.
- TZ-aware timestamps; favicon; ASCII art banner; active-page nav indicator.
- Alembic migrations scaffolded (`alembic/`, `alembic.ini`).
- Partial-tag write helpers (`tagging/partial.py`) used by individual library actions.
- Health endpoint `GET /health` (no auth) + Docker `HEALTHCHECK`.
- GitHub Actions CI (`test` + `docker` jobs publishing to GHCR).
- First-run setup wizard at `/setup` for credential bootstrap without Docker secrets.
- terminal24 theme: IBM Plex fonts, CRT scanlines, zero border-radius, pastel status badges.

### Changed
- Project rename: `aio_tagger` → `dragontag`; port `8080` → `7593`; env-var prefix `AIO_` → `DRAGONTAG_`.
- Default Vorbis separators: `//` for ARTIST/album_artist, `;` for ARTISTS and MB IDs.
- Watcher: event-driven settle loop replaces busy-poll.
- Scanner batches DB commits in 50-row transactions; rolls back per-file failures without aborting the batch.
- MusicBrainz client: exponential backoff retry on `WebServiceError`; `release-groups` include removed from `fetch_recording`.
- Pipeline: `enqueue()` deduplicates against active jobs at the same path; cover-art guard skips CAA fetch when user supplied bytes; RELEASETYPE inferred from track count when MB omits it; `release_status` defaults to `Official`.
- Session cookie lifetime extended to 7 days.
- Lazy imports for Pillow and requests (faster cold start).
- Container hardened: uid 1000, read-only root, `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp`.

### Fixed
- Duplicate enqueue when upload handler and watcher both fired on the same drop-folder write.
- MB `release-group.type` missing → inferred fallback from track count.
- Upload validation: extension + MIME + non-zero-byte checks; executables rejected with HTTP 422.

### Removed
- `TASKS.md`, `SESSION_HANDOFF.md` (obsolete).
- Local dev defaults / internal paths from shipped configs.
