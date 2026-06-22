<!-- AGENTS: Group new work under the current WIP heading. Historical sections below are summarized — do not expand them. -->

# Changelog

## WIP — terminal/TUI frontend redesign (Direction A)

### Changed
- **Full UI restyle into a lazygit-style monochrome terminal vocabulary.** All 18 templates
  (7 nav pages + `login`/`setup` + secondary pages + 5 htmx fragments) reworked with `.dt-*`
  texture primitives (titled panels with corner reticles, notched labels, blinking cursor,
  meter bars), bracketed `[ label ]` buttons, and text+glyph status (`● done · ▲ review · ✕ error`)
  instead of filled badge chips. Green is reserved for *meaning* only (done/active/focus/progress);
  amber for review, red for errors.
- **Primary face is now JetBrains Mono** (vendored `JetBrainsMono-Regular/Bold.woff2`), body
  switched `font-sans` → `font-mono`. `fonts.css` falls back to the already-vendored IBM Plex
  Mono when the JetBrains files are absent, so the UI is never in a broken/missing-glyph state.
- **`frontend/app.input.css`** gains an `@layer components` block with the reusable `.dt-*`
  primitives; **`frontend/tailwind.config.js`** uses a JetBrains-first mono stack and a wider
  safelist for dynamically-applied classes.
- **Keybind status bar** (`{% block statusbar %}` in `base.html`) is pinned to the viewport
  floor (`position: fixed` + body `pb-[30px]`).

### Preserved
- Every `hx-*` attribute, Alpine directive, input `name`/`id`, form action, `confirm()` handler,
  and inline `<script>` carried over verbatim — purely presentational change. Review-apply,
  cover picker, bulk-apply gather, cron-describe, settings token palette + dirty-state guard,
  toast/progress JS, and drag/drop upload wiring all unchanged.

## 0.9.5 — repo housekeeping & UI polish (2026-06-15)

### Changed
- **Front-end build toolchain grouped under `frontend/`** — `app.input.css`, `tailwind.config.js`, and `scripts/build_css.sh` moved to `frontend/`; `scripts/` directory removed. Run `bash frontend/build_css.sh` to rebuild `app.css`. Reference in `base.html` updated.
- **Toast notifications** deduped (single `showToast` delivery path; belt-and-suspenders 2-second key guard), duration extended to 8 s, fade-in/out added via Alpine `x-transition.opacity.duration.400ms`.
- **Dashboard "Average length"** now reflects pipeline-tagged tracks — `_upsert_track` reads and persists `Track.duration` from the freshly-written file on both create and update paths.
- **Settings "Save settings" button** moved to `fixed top-[80px] right-4` so it floats at the page's right margin rather than the right edge of the narrow centred column.
- **Dashboard** gains a page-scoped `overflow-y: hidden` style to remove the vertical scrollbar.
- **Docs anchor links** no longer overshoot behind the sticky nav — `scroll-padding-top: 80px` + `scroll-margin-top: 80px` on `section[id]` added via a scoped `<style>` block.
- **Version bumped to 0.9.5** across `pyproject.toml`, `dragontag/app/__init__.py` (new `__version__`), FastAPI app title, MusicBrainz User-Agent fallback, and tagging schema fallback. Swagger `/api-docs` now reports `0.9.5`.

---

## Unreleased — data-integrity & resilience sweep (2026-06-14)
**Branch:** `claude/affectionate-sagan-41telq`

### Fixed
- **Tag writes are now atomic (prevents audio-file corruption)** — every in-place mutagen save (full writers, `tagging/partial.py` single-field updates, and `tagging/snapshot.py` revert) is performed on a same-directory temp copy and atomically `os.replace`d in via the new `tagging/writers/_atomic.atomic_inplace` helper. A crash mid-save can no longer truncate the user's only copy of a track. FLAC/MP4 also drop a redundant `delete()` on-disk write in favor of an in-memory clear. `write_cover_jpg` is atomic too. (`tagging/writers/*`, `tagging/partial.py`, `tagging/snapshot.py`, `library/mover.py`)
- **Outbound API calls can no longer hang the worker** — new `network_timeout_seconds` setting (default 15s) is applied as the urllib socket default for MusicBrainz and as the AcoustID `lookup` timeout; without it a half-open connection could freeze the single ingest worker (and, via the same-kind check, all scheduled work) indefinitely. AcoustID failures now degrade to no-match for any error, not just `WebServiceError`. (`config.py`, `identify/musicbrainz.py`, `identify/acoustid.py`)
- **Stalled/hung background tasks now self-recover** — `tasks.reap_stale_jobs()` marks any `running` Job whose progress heartbeat (`updated_at`) hasn't advanced for 15 minutes as `error`; the scheduler runs it each tick so a wedged task can't block its task type forever. (`tasks.py`, `scheduler.py`)
- **Partial-transfer ingestion** — the drop-folder watcher now requires a file's size to be stable across the settle window before enqueuing, so a slow/stalled SMB/NFS copy isn't read half-written. (`ingest/watcher.py`)
- **Robustness** — `assemble_tags` tolerates malformed MB artist-credits instead of `KeyError`-bouncing a tag-able file to review; `existing_tags.read` degrades to empty clues on a corrupt header instead of erroring the whole job; `enqueue` serializes its dedup check-then-insert (no duplicate jobs); `organize_folder` escalates an unrecoverable file/DB divergence to a critical log and surfaces it in the summary; `move()` verifies destination size and survives a vanished source during the `samefile` check; scoring NFC-normalizes + casefolds and treats a 0-second duration as valid. (`identify/musicbrainz.py`, `identify/existing_tags.py`, `ingest/pipeline.py`, `library/organizer.py`, `library/mover.py`, `identify/scoring.py`, `identify/filename_parse.py`)

### Tests
- New suites: `test_atomic_writes.py` (failure-injection leaves originals intact), `test_existing_tags_corrupt.py`, `test_musicbrainz_credits.py`, `test_watcher_settle.py`, `test_tasks_reaper.py`, `test_scoring_unicode.py`, `test_mover_verify.py`. Suite: 169 passing.

---

## Unreleased — codebase bug-fix sweep (2026-06-13)
**Branch:** `claude/clever-ramanujan-d4dzwm`

### Fixed
- **Scanner could silently drop files (data loss)** — when indexing a folder, one unreadable file in a 50-file batch triggered `s.rollback()`, discarding every already-upserted Track in that batch; the final commit then persisted only files added after the failure. Each file now gets its own SAVEPOINT (`s.begin_nested()`), so a bad file loses only itself. (`library/scanner.py`)
- **Partial tag actions crashed on WAV files** — `tagging/partial.py` imported `mutagen.wav` (no such module; it is `mutagen.wave`), so *Fetch lyrics*, *Tag advisories* and *Fetch cover art* raised `ModuleNotFoundError` on every `.wav`. Fixed all four call sites.
- **ID3 sort names written twice** — `ARTISTSORT`/`ALBUMARTISTSORT` were emitted both as the canonical `TSOP`/`TSO2` frames *and* as redundant `TXXX` frames. Removed the duplicate `TXXX` entries. (`tagging/writers/_id3common.py`)
- **Cover art consistency** — the case-sensitive `"png" in mime` check mis-encoded `image/PNG` cover art as JPEG (fixed to be case-insensitive across FLAC/MP3/WAV/MP4); the *Fetch cover art* action embedded un-resized full-resolution images (now routed through the shared 1200px `_cap_cover`); and the duplicate `_cap_cover` copy in `flac.py` was consolidated into `_id3common.py`.
- **Grammar correction corrupted common words** — the contraction map rewrote valid standalone words (`were→we're`, `well→we'll`, `wed→we'd`, `ill→I'll`, `id→I'd`), mangling titles like "We Were Young". Those ambiguous entries were removed. (`tagging/formatter.py`)
- **File/DB divergence on commit failure** — `organize_folder` and `revert.move_back` moved a file on disk before updating its `Track.path` in a separate transaction; a failed commit left the library pointing at a path the file no longer occupied. Both now roll the file back (compensating move) on DB failure, and `move_back` persists the DB record before touching the persistent exclude-list setting. The lyrics/advisory DB-sync failures in `library/actions.py` are now logged explicitly instead of masquerading as write failures.
- **Scheduler `dry_run` not normalized** — the `bulk_retag` task passed the raw `params_json` value through, while the parallel `batch_retag` used `bool(...)`; a stored `"false"`/`0`/`None` could mis-trigger. Also fixed `_tick` gating on a stale `enabled` flag instead of the freshly-fetched row. (`scheduler.py`)
- **Silent exception swallows** — `review_bulk_apply` now counts and logs per-job failures and surfaces them in the toast; `dashboard_stats` logs its top-artists query failure instead of swallowing it. (`main.py`, `db.py`)

### Tests
- New regression suites for the scanner batch rollback, organizer/revert file-vs-DB consistency, cover-art capping + ID3 sort frames, and scheduler `dry_run` normalization; extended the grammar tests to lock in the contraction fix. (`tests/test_{scanner_batch,organizer,revert_move_back,writers_cover,scheduler}.py`, `tests/test_grammar.py`)

---

## 0.9.0 — scan-filter merge, task stopping & UI polish (2026-06-10)
**Branch:** `feature/0.9.0-polish`

### Added
- **Stop running tasks** — running background tasks (scan, organize, batches, …) can now be stopped: a per-row **stop** button on the Queue page and a **stop** control on the universal progress bar (both with confirmation). `tasks.py` gained per-job cancel events (`request_cancel`, `TaskCtx.cancelled/check_cancelled`, `TaskCancelled`); the scanner, organizer and `run_chain` check cooperatively. A stopped task keeps the work done so far and is marked `skipped` with a "Stopped by user." log line. `POST /jobs/{id}/cancel` now accepts running task jobs; `GET /api/progress` reports `job_id`/`stoppable`.
- **Clear all scan filters** — one button (with confirmation) empties filter patterns, excluded directories and excluded files (`POST /settings/clear-scan-filters`, replacing `/settings/clear-scan-exemptions`).

### Changed
- **Scan exemptions merged into Scan filters** — `scan_exempt_paths` is replaced by the editable `scan_exclude_files` list (third textarea in Settings → Scan filters). "Move back" on the Changes page auto-adds the restored path there (FIFO-capped at 500); the watcher/scanner/bulk re-tag all go through `filters.is_path_excluded`, which now also takes excluded files. Existing `scan_exempt_paths` entries in `settings.json` are migrated on first load.
- **Excluded directories: SLSKD-style `!` prefix removed** — plain absolute paths only; the UI no longer prepends `!` for display.
- **Nav reordered** — Queue moved between Library and Changes; Docs is now last (right of Settings); Log out stays right-aligned.
- **Docs page** — title renamed to "dragontag | Docs"; the `openapi.json` header button removed (Swagger link stays; the endpoint itself is unchanged).
- **Dashboard banner** — dropped the "identify — tag — organize" tagline and fixed the slightly off-center art (stray trailing whitespace line inside the `<pre>`).
- Version bumped to **0.9.0**.

### Fixed
- **Scan filter settings never saved** — the Scan filters card sat *outside* the settings `<form>`, so its textareas were never submitted and every save reset both lists to empty. The card now lives inside the form.

---

## Unreleased — queue merge, batch operations & UX sweep (2026-06-10)
**Branch:** `claude/youthful-clarke-f181sj`

### Added
- **Queue page** (`/queue`) — Review and Jobs merged into one page: the needs-review section (candidate picker, manual MB search, conflict resolver, bulk apply) on top, the full job list (stats, bulk controls, pagination) below. Old `GET /review` / `GET /jobs` 308-redirect to it; `/jobs/{id}` detail and all POST routes keep their paths. Nav shows a single **Queue** item immediately left of Log out.
- **Batch operations** — three one-click chains per library folder: **Organize** (organize files → fix disc folders → normalize filenames → extract covers → prune junk → find duplicates → find missing tracks), **Full re-tag** (validate tags → advisories → ReplayGain → full identify→tag→move pipeline, with per-run dry-run), and the **Nuclear option** (both). Also schedulable (`batch_organize` / `batch_retag` task types).
- **`tasks.run_chain`** — runs several actions sequentially under one Job with `[i/n] step` prefixes on every log line and progress label; a failing step is logged and the chain continues (Job errors only when every step fails).
- **Four new individual library actions** — *Normalize filenames* (extension casing, trailing dots/spaces), *Find duplicates* (MB recording ID + artist/title/duration match, report-only), *Prune junk & empty folders* (Thumbs.db/.DS_Store/*.tmp + empty dirs; audio never touched), *Validate tags* (missing core fields, mojibake, impossible track/disc numbers, report-only).
- **Multi-select action queueing** — checkboxes on the individual library actions + **Queue selected** run any combination as a single sequential chain (`POST /library/run-selected`); a registry (`LIBRARY_ACTIONS`) drives buttons, chains and batches.
- **Incomplete albums tab** — `find_missing_tracks` now persists results to the new `IncompleteAlbum` table (delete-then-insert per folder, including the missing track titles); rendered at `/library/incomplete` with MB links, per-row dismiss and re-check buttons.
- **Real progress reporting** — extract-covers, ReplayGain (now per album folder), verify-integrity, fix-disc-folders, find-missing-tracks and tag-advisories converted from anonymous daemon threads to `tasks.run_task`; `TaskCtx.progress()` gained an `item` label persisted to `Job.progress_item`. `GET /api/progress` returns `current/total/item`, and the progress bar shows the percentage, counts and current file instead of an endless pulse.
- **Genre junk filter** — MusicBrainz community tags are filtered against a vendored canonical genre list (~1500 entries, `identify/data/genres.txt`, from beets' lastgenre, MIT) before the genre limit applies, with hyphen/space-insensitive matching and a junk-blacklist fallback when nothing whitelists. New `genre_whitelist_enabled` setting (default on). Kills tags like "billboard top 100".
- **Cron descriptions** — the Schedule form live-translates cron expressions to plain English ("At 06:00, only on Tuesday") via the new `cron-descriptor` dependency and `GET /api/cron-describe`; every schedule row shows its description too.
- **Settings UX** — the Save button is now sticky at the top right of the form, glows red (with an "unsaved changes" hint) while the form is dirty, and a `beforeunload` warning prevents losing edits; the old bottom button is gone. The filename-template preview now also renders a multi-disc example (`Disc 2/07. Song Title.flac`), and every Skip-fields checkbox has an explanatory tooltip.
- **Renamed** "Clear needs_review" → **"Clear Review Queue"**; new block-shadow dashboard ASCII banner.

- **Scan filters** — two user-configurable lists in Settings → Scan filters: regex patterns matched against filenames (e.g. `\.ini$`, `Thumbs\.db$`) and excluded directory paths (absolute, SLSKD-style `!` prefix accepted). Both are applied by the drop-folder watcher, library scanner, and bulk re-tag. 9 unit tests in `test_scan_filters.py`.

### Files changed
Modified: `dragontag/app/{config,db,main,models,scheduler,tasks}.py`, `dragontag/app/identify/musicbrainz.py`, `dragontag/app/library/{actions,organizer}.py`, `dragontag/app/ingest/{bulk,watcher}.py`, `dragontag/app/library/scanner.py`, `dragontag/app/web/templates/{base,dashboard,docs,library,schedule,settings,_jobs_table}.html`, `pyproject.toml` (+`cron-descriptor`, package-data), `README.md`.
New: `dragontag/app/identify/{genres.py,data/genres.txt}`, `dragontag/app/library/filters.py`, `dragontag/app/web/templates/{queue,library_incomplete}.html`, `tests/test_{routes_queue,tasks_chain,genre_filter,library_actions_new,incomplete_album,cron_describe,scan_filters}.py`, `PLAN.md`.
Removed: `templates/{jobs,review}.html` (absorbed into `queue.html`).

---

## Unreleased — scheduling, backup/restore, progress & job-tracking sweep (2026-06-09)
**Branch:** `claude/exciting-pasteur-g0gphi`

### Added
- **Schedule tab** (`/schedule`) — cron-standard scheduling (5-field expressions via `croniter`) for scan / organize / bulk re-tag / fetch lyrics / fetch covers / backup, with run-now, enable/disable, next/last-run display and validation. New: `scheduler.py`, `models.ScheduledTask`, `templates/schedule.html`.
- **Backup / restore** — `GET /backup/download` exports a versioned tarball (manifest + sha256) of the SQLite DB (consistent snapshot via the sqlite backup API), `settings.json`, password hash and AcoustID key; the last 10 also land in `/config/backups`. Restore via validated upload in Settings (refused while jobs are active; old files kept as `*.pre-restore`) or `python -m dragontag.tools.restore_backup` when the UI is down. New: `backup.py`, `tools/restore_backup.py`.
- **Universal progress bar** — thin line pinned under the nav on every page, polling the new `GET /api/progress` endpoint (percent when known, pulse when indeterminate, queued count).
- **Background tasks are tracked Jobs** — scan, organize, fetch-lyrics and fetch-covers now run through the new `tasks.run_task` runner: each gets a Job row with a `kind` badge, persistent log and `n/total` progress on the Jobs page. Tasks interrupted by a restart are marked `error` instead of silently lost. New: `tasks.py`, `Job.kind/progress_current/progress_total`, `JobStatus.running`.
- **Move back + scan exemptions** — the Changes page gained a per-row **Move back** that returns a file to its pre-pipeline directory and auto-adds it to the new `scan_exempt_paths` setting, honored by the watcher, scanner and bulk re-tag (viewable/clearable in Settings). Also a **Clear all** button and a configurable retention cap (`max_recent_changes`, 0 = unlimited) replacing the hard-coded 500.
- **Log verbosity slider** — 0–4 (silent/errors/warnings/info/debug) in Settings, persisted to `settings.json` and applied at runtime (`logsetup.py`); noisy third-party loggers stay capped at WARNING.
- **OpenAPI access** — auth-guarded `GET /openapi.json` + Swagger UI at `GET /api-docs`, linked from the user-manual header (the manual keeps `/docs`).
- **Explicit Search button** in the review queue's manual MB matching (plus Enter-to-search); no more per-keystroke queries, and Enter can no longer accidentally submit the apply form.
- **Remove from library** — per-row × in the Library table deletes a stuck Track DB row only (the file is untouched; a re-scan re-adds it).

### Fixed
- **Dry-run checkbox never honored** — the Library page checkboxes silently wrote the global `dry_run` setting (and an unchecked box couldn't turn it off, so it appeared stuck on). They are now per-run only via `Job.dry_run_override` and never touch the global flag, which is set solely on the Settings page.
- **Re-tag after revert failed with "Source file not found"** — reverting (and moving back) now repairs the originating Job's `source_path`/`destination_path` to the file's current location so a requeue works.

### Files changed
Modified: `dragontag/app/{config,db,main,models}.py`, `dragontag/app/ingest/{bulk,pipeline,watcher}.py`, `dragontag/app/library/{actions,revert,scanner}.py`, `dragontag/app/web/templates/{base,changes,docs,library,review,settings,_jobs_table,_library_tracks}.html`, `pyproject.toml` (+`croniter`), `README.md`, `CHANGELOG.md`.
New: `dragontag/app/{backup,logsetup,scheduler,tasks}.py`, `dragontag/app/web/templates/schedule.html`, `dragontag/tools/restore_backup.py`.

---

## Unreleased — dashboard counters, MB matching, foldering & cover bleed (2026-06-08)
**Branch:** `task/dashboard-mb-folders-coverart`

### Added
- **Manual MB matching by artist & album, plus by URL/ID** — the Review-queue manual search now has separate **Title / Artist / Album** fields (instead of one box) and a **"MusicBrainz URL / ID"** field that resolves a recording link to its releases, a release link to its tracks, or a bare MBID. Search results now show the artist so songs with common titles can be told apart. New `musicbrainz.candidates_from_mbid()`.
- **`has_lyrics` on Track** — populated from the file's own lyrics tags during scan/tag so the dashboard can count real lyrics.
- **Two new settings** (Settings page): `cover_allow_release_group_fallback` (default **off**) and `folder_artist_split_separators` (default **empty**).

### Changed
- **Folders group by primary album-artist** — the artist folder segment now always strips featured guests (`feat./ft./featuring …`), so "Artist feat. Guest" files under "Artist". Genuine multi-artist credits ("A & B", "A, B") are only reduced to the first artist when the user opts in via `folder_artist_split_separators`; `//` and `/` are never split, so "AC/DC" and "A//B" stay combined. New `paths.primary_artist()`. Re-run **Organize** to re-folder existing files.
- **Dashboard "Tracks with lyrics" counts real lyrics** — was a proxy on `advisory IS NOT NULL` (always 0 for scanned libraries); now counts `Track.has_lyrics`.

### Fixed
- **Dashboard "Explicit" / "Lyrics" counters showed 0 for scanned libraries** — the scanner never read advisory/lyrics tags. `identify/existing_tags.read()` now extracts `ITUNESADVISORY`/`rtng` (normalized: iTunes `2`=clean → `0`) and detects `USLT`/`LYRICS`/`©lyr`, and the bulk fetch-lyrics / tag-advisories routes update the DB so counts refresh without a re-scan.
- **Same cover art applied to different albums** — the auto-pipeline's release-group cover fallback (one image shared across every edition in the group) is now gated behind `cover_allow_release_group_fallback` (default off). The Review UI also resets a previously-clicked cover thumbnail when the matched candidate changes, so the embedded cover always follows the chosen release.

### Files changed
Modified: `dragontag/app/{config,db,models}.py`, `dragontag/app/identify/{existing_tags,musicbrainz}.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/library/{paths,scanner}.py`, `dragontag/app/main.py`, `dragontag/app/web/templates/{review,_mb_search_results,settings}.html`, `tests/test_paths.py`, `README.md`, `CHANGELOG.md`.
New: `tests/test_existing_tags.py`, `tests/test_musicbrainz.py`.

---

## Unreleased — Navidrome multi-value, revert & queue sweep (2026-06-07)
**Branch:** `task/navidrome-revert-sweep`

### Added
- **Change history + revert** — every pipeline tag-write now records a `FileChange` row with a full pre-write tag snapshot. The new **Changes** page (`/changes`) lists recent changes with a per-row **Revert** that restores the file's original tags in place and removes the `cover.jpg` dragontag added (it does not move the file back). New: `tagging/snapshot.py`, `library/revert.py`, `models.FileChange`, `templates/changes.html`. History is pruned to the most recent 500 rows.
- **Jobs queue per-row select + Clear selected** — per-row checkboxes + "Select all" and a `POST /jobs/clear-selected` route that deletes the chosen rows (in-flight jobs are skipped).

### Changed
- **Multi-value tags are written as native multiple values** — ARTIST, ALBUMARTIST, ARTISTS, GENRE, sort names, composer/conductor/lyricist/arranger, LABEL, ISRC and the MusicBrainz artist-id lists now render as one Vorbis comment / ID3v2.4 multi-value / MP4 list entry **per value** instead of a single `"a//b//c"` string. Navidrome and Picard split these into separate artists/genres. Added `TrackTags.album_artists` (populated from MusicBrainz). The per-tag `Separators` are no longer used to join these fields.
- **Docs nav opens the built-in user manual** — FastAPI's Swagger UI / ReDoc / OpenAPI schema are disabled (`docs_url`/`redoc_url`/`openapi_url=None`) so the custom `/docs` route is reachable instead of being shadowed by Swagger.

### Fixed
- **Review-queue manual MB search could 422** (`recording_id`/`release_id` "missing"). The apply handler now resolves the chosen ids server-side from the selected radio (`pick`) or the manual id inputs, treats those form fields as optional, and bounces back with a toast instead of a 422 when nothing is chosen. Search-result radios are explicitly associated with the apply form via `form=`.
- **WAV tagging was broken** — `populate_id3` called `id3.delete()`, which WAV's `_WaveID3` rejects (it requires a positional `filething`). Switched to `id3.clear()`, which clears frames in-memory and also avoids redundant file I/O for MP3.

### Files changed
Modified: `dragontag/app/main.py`, `dragontag/app/models.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/identify/musicbrainz.py`, `dragontag/app/tagging/schema.py`, `dragontag/app/tagging/writers/_id3common.py`, `dragontag/app/tagging/writers/mp4.py`, `dragontag/app/web/templates/{review,_mb_search_results,jobs,base}.html`, `tests/test_schema_vorbis.py`, `README.md`, `CHANGELOG.md`.
New: `dragontag/app/tagging/snapshot.py`, `dragontag/app/library/revert.py`, `dragontag/app/web/templates/changes.html`, `tests/test_snapshot.py`, `tests/test_writers_multivalue.py`.

---

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
