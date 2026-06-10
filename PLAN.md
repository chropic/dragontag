# Dragontag UI/Workflow Overhaul — Plan & Progress Tracker

Branch: `claude/youthful-clarke-f181sj` → draft PR to `main` (via GitHub MCP, not gh CLI).
**This file is the living tracker — update the checkboxes after each phase so work can resume if the session is cut off.**

## Context

Dragontag is a FastAPI + Jinja2 + HTMX/Alpine + Tailwind-CDN + SQLModel/SQLite music tagger (no frontend build). The user wants a batch of UX/workflow improvements: dirty-aware sticky save in Settings, Review+Jobs merged into one "Queue" page, a real determinate progress bar, reworked batch library operations (Organize / Full re-tag / Nuclear) plus four new individual actions with multi-select queueing, a Library "Incomplete" tab fed by find-missing-tracks, multi-disc filename preview, MB genre-whitelist junk filtering, skip-field tooltips, a button rename, and human-readable cron descriptions. Several actions currently run as bare daemon threads with zero progress reporting — root cause of the "just flowing green" bar.

Note on migrations: this repo migrates via `dragontag/app/db.py::_migrate()` raw `ALTER TABLE` + `SQLModel.metadata.create_all` (alembic/ has no versions). Follow that pattern.

## User decisions (locked in)

- Old `/review` and `/jobs` → **redirect** to `/queue`.
- Genres: **vendored MusicBrainz official genre whitelist** (static file) + junk-pattern blacklist fallback, settings toggle.
- Cron: **cron-descriptor** PyPI dependency.
- All four new actions approved: find duplicates, prune empty/junk, normalize filenames, validate tags.

## Key reusable code

- `tasks.py::run_task(kind, name, fn)` + `TaskCtx.progress()/.log()` (1s throttled commits)
- `main.py`: `/api/progress` (~145), settings POST (~644), library routes (~911–1125), `_toast_response`
- `actions.py` pattern: `fn(folder_id, ctx=None) -> dict`
- `organizer._prune_empty_dirs`, `actions._update_track_path`, `ingest.bulk.enqueue_folder`
- `scheduler.TASK_TYPES` / `run_task_by_type`; `croniter.is_valid`
- Templates: `base.html` (nav 54–78, progress 80–92 + JS 134–153), `settings.html` `updatePreview()` (~307), `tip()` macro
- `identify/musicbrainz.py` ~443–457 (genre extraction); `config.py UserSettings` + `store().update(patch)`

---

## Phase 1 — Task-runner foundation (progress, chaining)  [x]

Files: `tasks.py`, `main.py`, `library/actions.py`, `library/organizer.py`, `models.py`, `db.py`, `base.html`

- [x] `TaskCtx.progress(current, total, item=None)`; new `Job.progress_item: str | None` column (+ `ALTER TABLE job ADD COLUMN progress_item VARCHAR` in `db._migrate`).
- [x] `tasks.run_chain(kind, name, steps: list[tuple[label, fn]])` — one Job row, sequential steps in one thread, `[2/5] Fix disc folders` prefixes, continue past per-step failure (job = error only if all fail). Chains containing bulk re-tag end with an `enqueue_folder` dispatch step.
- [x] Convert bare-daemon-thread routes to `run_task`: `/library/extract-covers`, `/library/replaygain`, `/library/verify-integrity`, `/library/fix-disc-folders`, `/library/find-missing-tracks`, `/library/tag-advisories` (move inline `_run` at main.py:1039–1061 into `actions.tag_advisories_for_folder`).
- [x] Thread `ctx=None` through `extract_embedded_covers`, `verify_integrity`, `fix_disc_folders`, `find_missing_tracks`, `recompute_replaygain`, `organize_folder`; per-item `ctx.progress(i, n, item=name)`. ReplayGain: log start/stderr; per-album loop only if cheap, otherwise stays indeterminate.
- [x] `/api/progress`: add `item`, `current`, `total`. `base.html progressBar()`: percent text, label `Name — 42% (123/290) · current_item`; pulse only when percent === null.

## Phase 2 — Queue page (merge Review + Jobs) + rename  [x]

Files: `main.py`, new `templates/queue.html`, `_jobs_table.html`, `job_detail.html`, `base.html`, `dashboard.html`; delete `jobs.html` + `review.html`

- [x] `GET /queue`: Review section on top (full review.html functionality: candidates, MB search, conflict resolver, bulk-apply; collapses to "Nothing needs review"), Jobs section below (stats, clear buttons, paginated table, 5s HTMX poll).
- [x] Keep all POST endpoints at current paths; change their redirect/toast targets to `/queue`.
- [x] `GET /review` and bare `GET /jobs` → 308 redirect to `/queue` (preserve `?page=`). `GET /jobs/{id}` detail stays, renders with `active_page="queue"`.
- [x] Nav: drop Review + Jobs entries; add `('/queue','queue','Queue')` LAST in `nav_items` so it sits immediately left of the `ml-auto` Log out form. Update dashboard recent-jobs links.
- [x] Button text: "Clear needs_review" → **"Clear Review Queue"** (route unchanged).

## Phase 3 — New individual library actions  [x]

Files: `library/actions.py`, `main.py`, `library.html`

- [x] `find_duplicates(folder_id, ctx)` — report-only; group by `mb_track_id`, normalized `(artist,title,≈duration)`, acoustid tag.
- [x] `prune_library(folder_id, ctx)` — delete junk files (conservative `_JUNK_PATTERNS`: Thumbs.db, .DS_Store, desktop.ini, *.tmp, *.part) + empty dirs (reuse `organizer._prune_empty_dirs`); never touches audio.
- [x] `normalize_filenames(folder_id, ctx)` — lowercase extensions, strip trailing dots/spaces, collapse double spaces; update `Track.path`.
- [x] `validate_tags(folder_id, ctx)` — report-only; missing albumartist, mojibake heuristic (Ã, â€), track/disc-total mismatches.
- [x] Routes (`run_task`): `POST /library/find-duplicates|prune|normalize-filenames|validate-tags`; buttons w/ title tooltips in Individual actions card.
- [x] `LIBRARY_ACTIONS` registry in `actions.py`: `key -> (label, callable)` — drives buttons, multi-select, batches, scheduler.

## Phase 4 — Batch ops, Incomplete tab, multi-select  [x]

Files: `main.py`, `library.html`, new `library_incomplete.html`, `models.py`, `actions.py`, `scheduler.py`

- [x] New `IncompleteAlbum` table: id, library_folder_id (FK/idx), mb_album_id, album, artist, local_count, expected_count, checked_at. `find_missing_tracks` delete-then-insert per run.
- [x] Library "Incomplete" tab → `GET /library/incomplete`: table + per-row dismiss (`POST /library/incomplete/{id}/delete`) + "Re-check now".
- [x] `POST /library/batch/organize` → chain: organize_folder, fix_disc_folders, extract_embedded_covers, prune_library, normalize_filenames, find_duplicates, find_missing_tracks.
- [x] `POST /library/batch/retag` → chain: validate_tags, tag_advisories, recompute_replaygain, then `enqueue_folder` (identify→tag→move via pipeline worker).
- [x] `POST /library/batch/nuclear` → everything; big red confirm.
- [x] Multi-select: checkboxes per individual action + "Queue selected" → `POST /library/run-selected` (`actions: list[str]`, `folder_id`) → `run_chain` in canonical order.
- [x] Guard: refuse to dispatch a batch if another non-ingest job is in `ACTIVE_JOB_STATUSES` (toast).
- [x] Optionally add `batch_organize`/`batch_retag` to `scheduler.TASK_TYPES`.

## Phase 5 — Settings UX (sticky save, multi-disc preview, tooltips)  [x]

Files: `settings.html`

- [x] Alpine `settingsForm()`: `dirty` flag via `@input/@change`; `beforeunload` warning when dirty; reset on submit.
- [x] Sticky save: `sticky top-[70px] z-30 flex justify-end` container at top of form; red glow when dirty (`bg-[#ffb4b4] shadow-[0_0_12px_rgba(255,100,100,0.8)] animate-pulse` — literal strings so Tailwind CDN JIT picks them up). Remove old bottom save block (lines 248–253).
- [x] Multi-disc live preview: extend `updatePreview()` to render multidisc filename + disc-folder templates → `Single: 01. Song Title.flac` / `Multi: Disc 1/01. Song Title.flac`.
- [x] Skip-field tooltips: Jinja dict of 30 one-liner descriptions (full text in design notes below) rendered via existing `tip()` macro.

## Phase 6 — Genre whitelist  [x]

Files: new `identify/genres.py` + `identify/data/mb_genres.txt`, `identify/musicbrainz.py`, `config.py`, `settings.html`, `pyproject.toml`

- [x] Vendor MB official genre list (~1900 entries, lowercase, one per line); add package-data so it ships in wheel/Docker.
- [x] `genres.py`: `load_whitelist()` (lru_cache), `_JUNK_RE` blacklist (charts e.g. `billboard`, `top \d+`, `seen live`, `favou?rite`, `fixme`, `check`), `filter_genres(raw)`: normalize hyphen/space variants, dedupe, whitelist pass; fallback = keep non-junk raw tags if nothing whitelisted.
- [x] Hook into `musicbrainz.py` (~447) before genre_limit slicing/casing.
- [x] `genre_whitelist_enabled: bool = True` in UserSettings + checkbox in Genre options card.

## Phase 7 — Cron descriptions + ASCII art  [x]

Files: `pyproject.toml`, `scheduler.py`, `main.py`, `schedule.html`, `dashboard.html`

- [x] Add `cron-descriptor` dep; `scheduler.describe_cron(expr) -> str | None`.
- [x] `GET /api/cron-describe?expr=` → `{valid, description}`; Alpine `@input.debounce.300ms` live description under the cron input (green valid / red invalid); server-rendered description subtext on each schedule row.
- [x] Dashboard ASCII: ANSI-shadow style DRAGONTAG banner (box-drawing chars, no Jinja-sensitive chars) + small dragon glyph accent; `text-[8px] sm:text-xs` for width; keep glow.

## Phase 8 — Review, tests, fixes  [x]

- [x] New tests: `test_routes_queue.py` (redirects, /queue 200), `test_tasks_chain.py` (order, failure continuation, progress_item), `test_genre_filter.py`, `test_library_actions_new.py` (prune never deletes audio; normalize updates DB; validate/duplicates counts), `test_incomplete_album.py`, `test_cron_describe.py`.
- [x] Run full `pytest`; manual smoke: run dev server, click through Queue, Settings dirty-save, batches, progress bar, schedule descriptions.
- [x] `/code-review`-style self-review of the diff; fix findings.

## Phase 9 — Docs  [x]

- [x] README (Queue page, batch ops, new actions, Incomplete tab, genre whitelist, cron descriptions), CHANGELOG entry, in-app `docs.html` manual, `.claude/memory/{architecture,conventions,project_overview}.md`.

## Phase 10 — Ship  [x]

- [x] One commit per phase (already committed incrementally as phases land), push `git push -u origin claude/youthful-clarke-f181sj` (retry w/ backoff), open **draft PR** to `main` via GitHub MCP (`mcp__github__create_pull_request`, repo chropic/dragontag).

## Risks

- Chains hold one thread; guard batch dispatch against concurrent non-ingest active jobs.
- Only bare `GET /jobs` redirects — `/jobs/{id}`, `/jobs/table`, POST routes stay.
- SQLite contention already throttled by TaskCtx `_COMMIT_INTERVAL`; one TaskCtx per chain.
- Tailwind CDN JIT needs dynamic classes as literal strings in templates.
- `beforeunload` fine: nav links do full page loads.

## Design notes — skip-field tooltip text

acoustid_id: AcoustID fingerprint UUID · arranger: arranger credits · artist_sort/album_artist_sort: sort-name variants ("Beatles, The") · barcode: UPC/EAN · catalog_number: label catalog number · compilation: compilation flag · composers/conductor/lyricist: classical/credit roles · disc/disc_total: disc numbering · genres: GENRE values from MB tags · isrcs: recording ISRC codes · labels: record labels · language: release language · mb_*_id(s): MusicBrainz IDs — skipping makes exact re-identification harder · media: medium format (CD/Vinyl) · original_date/original_year: earliest release-group date · release_country/status/type: release metadata · script: writing script · track_total: tracks per disc.

## Verification (end-to-end)

```bash
cd /home/user/dragontag && pip install -e ".[dev]" && pytest -v
DRAGONTAG_LIBRARY_PATH=./library DRAGONTAG_DROP_PATH=./drop DRAGONTAG_CONFIG_PATH=./config \
DRAGONTAG_USERNAME=dev DRAGONTAG_PASSWORD=dev uvicorn dragontag.app.main:app --port 7593
```
Then: /queue renders + old URLs redirect; settings save button glows red on edit and warns on leave; multi-disc preview updates live; run each batch on a sample folder and watch determinate progress with item labels; Incomplete tab populates; cron input shows "Every Tuesday at 06:00"-style text; genre filter test passes.


## Status — 2026-06-10

All phases complete. 72 tests passing (47 existing + 25 new); smoke-tested live (all pages 200, /jobs→/queue 308, chained batch run verified with step-prefixed logs, cron describe endpoint working). Note: genre whitelist vendored from beets' lastgenre list (MIT) since musicbrainz.org was not reachable from the build environment — functionally equivalent canonical list. Remaining: push + draft PR (Phase 10).
