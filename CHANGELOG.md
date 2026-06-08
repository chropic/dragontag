<!-- AGENTS: Group new work under the current WIP heading. Historical sections below are summarized — do not expand them. -->

# Changelog

## Unreleased — code-review fixes (2026-06-07)
**Branch:** `task/code-review-2026-05-28`

### Fixed
- **Review queue Apply/Commit was broken (HTTP 500).** The buttons' `onclick` called `form.submit()`, which does *not* fire the `submit` event — so the handler that copies the chosen MusicBrainz recording/release id into the hidden form fields never ran, and every Apply/Commit posted empty ids. Switched to `form.requestSubmit()`. (Regression from the 0.1.5 bfcache button change.)
- **Cover art desync on manual picks.** When a job had stored candidates, choosing a manual MB-search result (a different release) still embedded the first candidate's cover. The submit handler now derives the cover from the chosen release unless a thumbnail was explicitly clicked or a custom file was uploaded.
- Numeric titles mangled by `_strip_track_num` — the regex now requires a `.`/`-`/`)` separator, so "99 Luftballons" / "7 Years" survive while "01. ", "14-", "03 - " prefixes are still stripped.
- `/api/mb-search` 500 (`IndexError`) when the stored `artists` value was a present-but-empty list.
- Manual MB search picks were ignored for jobs with no stored candidates — the hidden `recording_id`/`release_id` are now always rendered and populated from a selected radio (candidate list or manual search) or the manual id-entry inputs.
- Cover decode could abort the whole tag write — `_cap_cover` now guards the PIL decode and falls back to the original bytes on failure (MP3/MP4/FLAC).
- Cover MIME/data mismatch — re-encoded covers now report the MIME of the bytes actually produced (also fixes MP4 `covr` format selection).
- Title-match track fallback now also sets `media_format`/`mb_releasetrack_id` and guards a missing `position`.
- Bulk re-tag "Select all in folder" from the all-folders view sent `""` and silently fell back to the page checkboxes; it now uses an explicit `all` sentinel.

### Added
- Regression test for `_strip_track_num` (`tests/test_track_num.py`).

### Files changed
Modified: `dragontag/app/identify/musicbrainz.py`, `dragontag/app/main.py`, `dragontag/app/tagging/writers/_id3common.py`, `dragontag/app/tagging/writers/flac.py`, `dragontag/app/web/templates/library.html`, `dragontag/app/web/templates/review.html`, `CHANGELOG.md`.
New: `tests/test_track_num.py`.

---

## [0.1.5] — plan-txt sweep (2026-05-27)
**Branch:** `task/plan-txt-sweep`

### Added
- **Manual MusicBrainz search in Review queue** — live HTMX search bar above each review item; seeds artist/album from the job's stored tags so results are scoped to the correct artist without extra typing.
- **Explicit advisory badge in library track table** — an "E" chip appears in each row when `advisory = 1`.
- **Progressive MB search fallback** — if a title + artist + album + duration query returns nothing, the search retries dropping album, then duration. Redundant retries are skipped when the dropped clause was absent to begin with.
- **Track-position title-match fallback** — when the recording UUID is missing from the MB release's track-list (rare data inconsistency), position is recovered by matching recording title instead.

### Changed
- **Cover-art cap now applies to all formats** — the 1200 px resize that previously guarded FLAC against `block is too long` now also runs for MP3, WAV (ID3 APIC frame) and MP4/M4A (`covr` atom).
- **Progressive MB search skips duplicate queries** — fallback attempts that would produce an identical Lucene query (because the dropped clause was already absent) are elided, cutting worst-case API calls from 3 to 1.
- **Review Apply/Commit buttons** restore their label and re-enable when the page is restored from the browser's back/forward cache (`pageshow` + `event.persisted`).
- **"Select all in folder" now shows a confirmation dialog** before submitting the bulk re-tag form.
- **Manual review search threads job context** — `/api/mb-search` accepts `job_id`; looks up stored `artist_display` and `album` from `chosen_tags_json` and passes them into the MB query for tighter results.
- **Sticky navigation bar** — `sticky top-0 z-50` added to `<nav>` so the header stays visible while scrolling long pages.
- **Dashboard upload zone** — file upload area now accepts drag-and-drop directly (drag highlight, auto-submit on drop); folder path input supports directory drag via `webkitGetAsEntry`; clear (×) button added.
- **musicbrainzngs log level** capped at `WARNING` to suppress INFO-level request noise.
- **Dynamic package version** in MB `User-Agent` via `importlib.metadata`; falls back to `"0.1.5"` when not installed as a package.
- Version bumped to `0.1.5`.

### Fixed
- `folder_id` int-parsing crash (`422 Unprocessable Entity`) when HTMX sort/filter links sent an empty `folder_id=` string — parameter now accepted as `str | None` and coerced manually.
- `write_tags failed: block is too long to write` on FLAC files with 3000 × 3000 cover art — now capped at 1200 px before embedding (fix extended to all other formats as well).
- `Source file not found` when requeueing a completed job — pipeline now falls back to `destination_path` before erroring when the original source has already been moved to the library.
- Intermediate pipeline log lines were invisible mid-job — `s.flush()` after the Clues log line makes them visible without breaking transaction atomicity.
- Log row HTML (`<pre>` injected directly into `<tr>`) was invalid and broke Chrome's layout — log content now targets a `<td>` wrapper cell.

---

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
