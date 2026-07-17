<!-- AGENTS: Group new work under the current WIP heading. Historical sections below are summarized — do not expand them. -->

# Changelog

## WIP — terminal/TUI frontend redesign (Direction A)

### Fixed (review queue: bulk apply + non-blocking apply — 2026-07-17)
- **Bulk apply works again and says what actually went wrong.** The bulk form's
  submit handler collected checked rows with `bulk.querySelectorAll(...)`, but
  the checkboxes are tied to the form via the `form=` *attribute* and are not
  DOM descendants — so per-job picks were silently dropped (a changed radio
  selection was ignored in favour of the stored top candidate) and items
  without stored candidates produced the misleading "select review items
  first" toast even with rows checked. The JS now queries the document for
  `[form=review-bulk-form]:checked`; the server distinguishes "nothing
  selected" from "selected items have no pick or stored candidate", applies
  the resolvable subset, and reports how many were skipped. (`web/templates/
  queue.html`, `main.py`)
- **Applying a review match no longer hangs the browser.** The single-apply
  route ran two MusicBrainz fetches, the cover and lyrics fetches, the tag
  write and the file move in the request thread. It now pre-flips the job to
  `tagging` (row leaves the review list immediately; a double-click is
  rejected) and runs the commit as a `review_apply` background task via the
  shared `_apply_review_match` closure (which gained release-type override +
  uploaded-cover support, and returns the job to review with a log line if
  the MusicBrainz fetch fails). Bulk apply pre-flips its selection the same
  way so a second submit can't double-apply. (`main.py`)

### Added (Completions page — 2026-07-16)
- **New `/completions` page: library health in one place.** Summary tiles
  (lyrics/tagged/covers/genres coverage meters, incomplete-album and
  duplicate counts) over seven lazy-loaded sections: incomplete albums
  (missing tracks vs MusicBrainz — the old Incomplete tab, which now
  redirects here), duplicate tracks (MB recording id, or same artist/title
  with agreeing durations) plus twin-looking album folders, songs without
  lyrics (per-row LRCLIB fetch), missing cover art and missing genres
  (snapshot from the new read-only `scan_health` action / `health_scan`
  job, stored in the new generic `HealthItem` table delete-then-insert per
  folder), untagged files, and tag problems. Live sections query the index
  on render; snapshot sections carry refresh buttons and per-row dismiss.
  Every row links into fixes (library search, MusicBrainz, track lyrics
  fetch, folder-level helpers) — nothing on the page moves or deletes
  files. Nav item + `g m` shortcut. The `find_duplicates`/`validate_tags`
  actions' logic moved into shared pure helpers (`duplicate_groups`,
  `duplicate_album_groups`, `tag_problems`) with unchanged action output.
  (`main.py`, `models.py`, `library/actions.py`,
  `web/templates/completions.html` + `_completions_*.html`,
  `web/templates/base.html`, `web/templates/library.html`)

### Fixed (retag no longer hangs the browser — 2026-07-16)
- **`POST /library/bulk-retag` returns immediately.** The folder walk and
  per-file job inserts now run as a background `retag` task (with progress +
  cancel on the Queue page) instead of inside the HTTP request thread, which
  stalled the browser for the duration of the walk on large folders. Path
  validation stays in-request for an instant error toast; htmx posts (the
  dashboard form) get a no-navigation toast, plain form posts a redirect.
  `_batch_guard` ignores running `retag` jobs — they only enqueue ingest rows.
  (`main.py`, `ingest/bulk.py`, `scheduler.py`)

### Changed (docs + agent memory for the new shape — 2026-07-16)
- **Docs describe the one-pass tagger.** README feature tables, the in-app manual
  (`docs.html`: Library section rewritten around Retag/Organize/helpers, new
  `destination_unresolved`/`album_mismatch`/`missing_releasetype` review-reason
  entries, schedule kinds updated) and stale batch references in templates. The
  damaged-library recovery recipe is documented as `Scan → Retag → Cleanup
  (apply)`. Agent memory (`.claude/memory/architecture.md`, `gotchas.md`,
  `testing.md`, `project_overview.md`) and `CLAUDE.md` hard rules updated:
  destination dirs are created only via `build_destination(ensure_dirs=True)`.
  (`README.md`, `web/templates/docs.html`, `web/templates/_track_edit_modal.html`,
  `web/templates/library_incomplete.html`, `CLAUDE.md`, `.claude/memory/*`)

### Fixed (cleanup: artist case-twins merged, covers never lost — 2026-07-16)
- **Cleanup now repairs artist-level case twins.** A new pass 0 groups
  top-level artist directories by strict `fold_text` equality (case, curly
  quotes, dash flavour — never fuzzy, so `jonatan leandoer96` and
  `Jonatan Leandoer127` stay separate) and merges `fakemink`/`Fakemink`-style
  twin trees into one elected target (most audio → majority `album_artist`
  spelling → deterministic), using the existing twin-merge machinery
  (relative sub-paths preserved, Track rows repointed per move, protected
  tracks skipped). Previously these twins — the source of the phantom-file
  breakage on case-insensitive share views — were unfixable by Cleanup.
  (`library/actions.py`)
- **Cleanup no longer quarantines visually distinct cover art.** Both the
  cover-dedupe pass and the twin-merge cover election now hash images and
  quarantine **only byte-identical duplicates** of the elected `cover.jpg`;
  a distinct losing image stays in (or moves into) the album folder under a
  unique name (`cover.old.jpg`, `img-1.png`, …). The old widest-wins
  quarantine emptied a real library's covers into `.dragontag-trash`.
  (`library/actions.py`)

### Removed (scope cut: one tagging pass — 2026-07-16)
- **The batch compositions and structural repair actions are gone.** Removed
  `BATCH_ORGANIZE` / `BATCH_RETAG` / `BATCH_NUCLEAR`, `build_chain_steps`, and
  the actions `fix_album_splits`, `check_album_consistency`,
  `unify_artist_folders`, `fix_disc_folders`, `normalize_filenames`,
  `reidentify_tracks` (plus their private helpers), along with their routes
  (`/library/batch/*`, `/library/run-selected`, `/library/fix-album-splits`,
  `/library/unify-artist-folders`, `/library/fix-disc-folders`,
  `/library/normalize-filenames`, `/library/reidentify`) and the Library page
  batch cards / multi-select chain UI. These were unattended file-movers with
  no dry-run — the class of tooling that nuked a real library. The ONE tagging
  pass is now the ingest pipeline: `/library/bulk-retag` (which also accepts a
  `folder_id`, powering the new Retag card) runs identify → tag → move with
  album-first identification and the review queue; `fix_album_splits`' release
  election lives on inside it (`ingest/album.py`). `LIBRARY_ACTIONS` shrinks
  to single-field backfills (covers/lyrics/advisories/genres/ReplayGain),
  read-only reports (validate/duplicates/missing/integrity), prune and
  cleanup. (`library/actions.py`, `main.py`, `web/templates/library.html`)
- **Scheduler kinds shrunk.** `TASK_TYPES` is now scan / organize / retag /
  fetch_lyrics / fetch_covers / cleanup / backup. The `batch_organize` and
  `batch_retag` kinds are retired: existing schedule rows are disabled (never
  deleted) at boot with an explanatory status; `bulk_retag` is accepted as a
  legacy alias for `retag` at dispatch. (`scheduler.py`, `main.py`,
  `web/templates/schedule.html`)

### Added (album-first identification — 2026-07-16)
- **Files ingested from one album folder are now identified as a unit.** Jobs
  enqueued together share a new `Job.group_key` (the album folder's resolved
  path; set by bulk re-tag for folders with ≥2 audio files and by the drop
  watcher for files arriving in a dropped subfolder — loose singles stay
  per-track). The pipeline elects ONE MusicBrainz release for the whole group
  (new `ingest/album.py`: per-file search candidates + pre-existing album ids
  — demoted from per-file short-circuit to weighted candidates — matched
  against full release documents; election ladder: coverage → Official →
  library-majority edition → larger edition → deterministic id) and assembles
  every member from that single release document, so ALBUMID/RELEASEGROUPID/
  ALBUMARTIST(+ID)/DATE/ORIGINALDATE/RELEASETYPE/RELEASESTATUS/MEDIA are
  identical across the album by construction — the root fix for albums
  splitting into multiple player listings. A group below the score threshold
  routes every member to review with the elected candidate pre-selected; a
  file not on the elected release routes to review with new reason
  `album_mismatch` instead of being silently forced onto the album; if no
  election is possible (MB down) the per-track path still runs. The election
  is memoized per group and recomputed when new members arrive (watcher
  settles files one at a time). (`ingest/album.py`, `ingest/pipeline.py`,
  `ingest/bulk.py`, `ingest/watcher.py`, `models.py`, `db.py`)

### Fixed (naming safety: case-twin prevention + unicode normalization — 2026-07-16)
- **Destination resolution is now race-proof and fail-closed.** The
  case-insensitive sibling reuse in `build_destination` and the directory
  creation that follows now run inside one global critical section
  (`ensure_dirs=True`), so two concurrent ingests of differently-cased artist
  spellings can no longer mint case-variant twin directories
  (`fakemink`/`Fakemink` — the failure that produced phantom files on
  case-insensitive views of a case-sensitive network share). An I/O error
  while scanning an existing library directory now raises
  `DestinationUnresolved` instead of silently pretending no sibling exists:
  the ingest pipeline routes the job to review (new reason
  `destination_unresolved`, with the in-place tag write recorded as a
  revertable `FileChange`) and the organizer skips the file. (`library/paths.py`,
  `library/mover.py`, `ingest/pipeline.py`, `library/organizer.py`, `models.py`)
- **Generated folder/file names are unicode-normalized.** `sanitize_segment`
  now applies NFC, strips zero-width/soft-hyphen characters, maps exotic
  dashes (U+2010/‑/–/—/−) and curly quotes to their ASCII equivalents, and
  defuses Windows reserved device names (`CON`, `COM1`…). Diacritics and
  non-Latin scripts are untouched — no ASCII folding. Stops MusicBrainz
  credit strings from materializing U+2010 hyphens in folder names
  (`Tay‐K`, Spider‐Man soundtracks). (`library/paths.py`)

### Fixed (cleanup/reidentify review — 2026-07-14)
- **Re-identify no longer burns a MusicBrainz text search per unmatched track.**
  The batch applies fingerprint-confirmed matches only, so `candidates_for_file`
  gained a `text_fallback` flag (the batch passes `False`) and skips the
  rate-limited MB search when the AcoustID fingerprint finds nothing — the
  interactive Identify route keeps the fallback. (`identify/relookup.py`,
  `library/actions.py`)
- **Cleanup report mode no longer double-counts deduped covers**, the twin-merge
  per-file loop is now cancellable, and the cover-promote path no longer attempts
  to quarantine an already-moved image. (`library/actions.py`)
- **New "Re-identify untagged tracks" library action** (`reidentify`, first step
  of the Re-tag batch, and in the Nuclear batch after album-split repair) —
  AcoustID-fingerprints every track that has no MusicBrainz recording id and
  applies the match in place (tags + cover). **Fingerprint matches only** —
  fuzzy text-search fallbacks are logged for manual review, never auto-applied.
  Skips protected tracks; writes tags in place without moving files. The
  per-track Identify lookup and apply-match write were refactored into shared
  helpers (`identify/relookup.candidates_for_file`, `library/retag.apply_match`)
  reused by both the route and the batch; the apply path keeps its
  network-before-write ordering and auditable `FileChange`/lyrics-carry
  semantics. (`identify/relookup.py`, `library/retag.py`, `library/actions.py`,
  `main.py`)

### Added (library cleanup with quarantine — 2026-07-14)
- **New "Cleanup" library action** (`cleanup`, `POST /library/cleanup`) — merges
  edition-suffix twin album folders (`Afraid` / `Afraid - Single` / `Afraid
  (Deluxe)`) into one elected target preserving each file's `Disc N` sub-path,
  dedupes cover art (keeps the widest `cover.jpg`), and **quarantines** dead
  folders and leftover non-audio files into `<library>/.dragontag-trash/<utc-ts>/`
  (or a configured `quarantine_path`). **Nothing is ever deleted and audio is
  never quarantined.** Report-only by default (safe in the Organize/Nuclear
  batches); the apply variant on the Library page moves files and is
  confirm-gated. The quarantine root is auto-excluded from future scans. Tag
  values are left untouched (`check_album_consistency`/`fix_album_splits` own
  tag agreement). Protected tracks are never moved; every move holds `path_lock`,
  branches on `MoveResult`, and commits `Track.path` per move. The dead-folder
  detection was refactored into a shared `_find_dead_folders`. Exposed on the
  Library page (report run + a confirm-gated apply card), as a schedulable task
  type (`cleanup`, with an apply toggle), and via two new settings
  (`quarantine_path`, `fold_edition_suffixes`) on the settings form.
  (`library/actions.py`, `config.py`, `main.py`, `scheduler.py`,
  `web/templates/library.html`, `web/templates/settings.html`,
  `web/templates/schedule.html`)

### Changed (edition-suffix folder folding — 2026-07-14)
- **Ingest now folds edition suffixes onto an existing base folder.** A file
  tagged `Afraid - Single` or `Afraid (Deluxe)` reuses an existing `Afraid`
  album folder instead of minting a suffixed twin next to it — the same
  convergence `_reuse_folded_dir` already did for case/punctuation variants,
  now extended with an edition-aware second pass gated on the new
  `fold_edition_suffixes` setting (default on). Among matching folders it
  prefers one that already holds audio, then the unsuffixed "base" name. The
  edition-suffix stripping primitives moved from `library/actions.py` to
  `library/paths.py` (`strip_edition_suffixes`, `album_fold_key`) as the shared
  home. Artist folders are never edition-folded. (`library/paths.py`,
  `library/actions.py`, `config.py`)

### Added (genre backfill — 2026-07-14)
- **New "Fix genres" library action** (`fix_genres`, `POST /library/fix-genres`,
  queued in the Re-tag batch) — backfills missing genres from MusicBrainz
  community tags for tracks that have none. Reads each file's embedded genre and,
  when empty, re-derives one from the recording's tags (falling back to the
  release-group's, which are far more often tagged) and writes it via a new
  single-field `write_genre` partial writer covering all four formats
  (FLAC `GENRE`, ID3 `TCON`, MP4 `©gen`) under `path_lock` + `atomic_inplace`.
  Only empty genres are filled — an existing genre is never overwritten — and
  tracks without a MusicBrainz id are left alone; the network fetch happens
  outside the file lock. (`library/actions.py`, `main.py`, `tagging/partial.py`,
  `web/templates/docs.html`, `README.md`, `tests/test_fix_genres.py`,
  `tests/test_partial_genre.py`, `tests/test_genre_derive.py`)

### Fixed (genres missing on ingest — 2026-07-14)
- **Release-group genre fallback now works.** `assemble_tags` intended to fall
  back from a recording's community tags to its release-group's, but a
  release-group nested in a release response never carries a `tag-list`, so the
  fallback was dead code and any recording without its own tags was tagged with
  no genre at all. Genre derivation is now a shared `derive_genres` helper, and
  the assembler fetches release-group tags explicitly (new `fetch_release_group`)
  whenever the recording derives to nothing — including recordings tagged only
  with junk. (`identify/musicbrainz.py`, `tests/test_genre_derive.py`)

### Added (project tooling — 2026-07-13)
- **SessionStart hook** (`.claude/hooks/session-start.sh`, registered in
  `.claude/settings.json`) — runs on every agent session start: enables the tracked
  git hooks (`git config core.hooksPath .githooks`) in *every* environment so the
  per-commit version bump is never missed, and on Claude Code on the web additionally
  builds the Python 3.12 venv, installs `-e ".[dev]"`, and puts `.venv/bin` on PATH so
  tests run without setup. Idempotent and non-interactive.
  (`.claude/hooks/session-start.sh`, `.claude/settings.json`)
- **Pull-request template** (`.github/pull_request_template.md`) — an accessible,
  self-explaining scaffold (summary, what/why, change type, how-tested, screenshots
  with alt-text guidance, reviewer notes, checklist) that GitHub pre-fills on every
  new PR. (`.github/pull_request_template.md`)
- **Per-commit versioning** — dragontag now bumps the patch version on every commit.
  A tracked `.githooks/pre-commit` runs `scripts/bump_version.py`, which increments
  `PATCH` in lockstep across `pyproject.toml` and both package `__init__.py` files
  and re-stages them. Enable once per clone with `git config core.hooksPath .githooks`.
  The version line was reset to `0.1.0` as the new baseline. Documented as hard rule 9 in
  `CLAUDE.md` and in the workflow memory / memory index so agents don't miss it.
  See `docs/VERSIONING.md`. (`.githooks/pre-commit`, `scripts/bump_version.py`,
  `docs/VERSIONING.md`, `CLAUDE.md`, `.claude/memory/workflow.md`, `.claude/memory/MEMORY.md`,
  `pyproject.toml`, `dragontag/app/__init__.py`)

### Added (duplicate artist/album folder cleanup — 2026-07-13)
- **New "Fix artist folders" library action** (`unify_artist_folders`,
  `POST /library/unify-artist-folders`, queued in the organize and nuclear batches
  before album-folder consistency) — collapses duplicate artist folders caused by
  capitalization (`fakemink`/`Fakemink`, `LUCKI`/`Lucki`), punctuation/Unicode
  (curly quotes, the Unicode dashes, `®`/`™`/`©` marks, `×`→`x`) and MusicBrainz
  alias/credit drift (`FERG`/`A$AP Ferg`) into one canonical folder per artist. It
  groups tracks by MusicBrainz album-artist id (falling back to a folded artist
  name), elects the majority album-artist spelling among the user's own files (a
  pure vote — stylized-lowercase names are kept, never forced to capitals),
  rewrites outlier `album_artist` tags and moves each file under the canonical
  folder while leaving its album untouched, then renames case-only folder variants
  (case-insensitive mounts) and prunes emptied folders. Offline; every move/tag
  write holds `path_lock` and commits per track; protected tracks are skipped.
  (`library/actions.py`, `main.py`, `tests/test_unify_artist_folders.py`)
- **New `Track.mb_album_artist_id` column** (idempotent `ALTER TABLE`), populated by
  the scanner and the ingest pipeline from the file's `MUSICBRAINZ_ALBUMARTISTID`,
  giving artist-folder unification a reliable key for alias variants that fold to
  different strings. (`models.py`, `db.py`, `identify/existing_tags.py`,
  `library/scanner.py`, `ingest/pipeline.py`)

### Changed (duplicate folder prevention — 2026-07-13)
- **Ingest now converges on an existing artist/album folder that differs only by
  case/punctuation instead of minting a duplicate next to it** — `build_destination`
  resolves each artist/album segment against the sibling directories already on disk
  (fold-equality only, never fuzzy), so a file tagged `Afraid` lands under an existing
  `afraid` folder rather than creating `Afraid`. This stops every future ingest from
  re-seeding the case-variant problem. (`library/paths.py`, `tests/test_paths.py`)
- **The offline album grouping used by "Fix album/folder consistency" and "Fix album
  splits" now folds case/punctuation/Unicode and strips iTunes-style trailing
  `- Single` / `- EP` suffixes and dangling dashes** (`…Friends–`), so `X` / `X (Deluxe)`
  / `X - Single` / `SPIDERR`/`Spiderr` variants of MB-less albums group together.
  (`library/actions.py`, `tests/test_unify_artist_folders.py`)
- **"Prune junk & empty folders" now also reports (never deletes) dead folders** — a
  directory with no audio anywhere below it but leftover files (`cover.jpg`, orphan
  `.lrc`), and album folders that contain only `Disc NN` subfolders with disc 1
  missing (the orphan-disc symptom). **"Validate tags" flags suspicious album names**
  (`_`, `(Deluxe)` with no base title). Both are report-only. (`library/actions.py`)

### Added (album-split repair — 2026-07-13)
- **New "Fix album splits" library action** (`fix_album_splits`, `POST /library/fix-album-splits`,
  also queued automatically as the first post-pipeline step of the nuclear batch) — repairs albums
  whose tracks were identified against *different editions* of the same MusicBrainz release group
  (different `MUSICBRAINZ_ALBUMID`s, album titles, track totals, barcodes and covers — rendered as
  several album listings by players). Per release group it elects a canonical release (the edition
  covering the most of the group's recordings, then Official status, then size, then a
  deterministic id tiebreak) and fully re-tags every track against it, preserving embedded
  lyrics/advisory and keeping existing art when no canonical cover is available, then merges the
  files into one canonical folder (conflict-safe moves, per-track DB commits, protected tracks
  skipped, edition-exclusive bonus tracks left as-is; groups with no MB ids fall back to the
  offline album/album-artist majority vote). (`library/actions.py`, `main.py`,
  `identify/musicbrainz.py`, `tests/test_fix_album_splits.py`)

### Fixed (album splitting — 2026-07-13)
- **Independent per-file identification scattered one album across MB editions** — near-tied
  search candidates (scores within 0.05) were picked by raw score order, so tracks of one album
  drifted onto different releases of the same release group. The pipeline now applies a consensus
  preference among near-tied candidates: Official status, then the release the library already
  uses for that release group (majority `mb_album_id`), then the larger edition, then a
  deterministic id tiebreak — so once one track of an album lands, its siblings follow. The
  auto-apply threshold still gates on the raw score leader. (`ingest/pipeline.py`,
  `tests/test_release_consensus.py`)
- **RELEASETYPE was inferred from the per-disc track count** — on releases without an MB
  primary-type, a small final disc (≤6 tracks) tagged its tracks "EP" while disc 1 said "Album",
  splitting the album. Inference now uses the release-wide track count (sum over all media),
  carried on a new internal `TrackTags.release_track_total`. (`ingest/pipeline.py`,
  `tagging/schema.py`, `identify/musicbrainz.py`)
- **MEDIA was the per-medium format** — mixed-format releases (CD+DVD, or a disc with no declared
  format) wrote different `MEDIA` values per track. It is now normalized release-wide: the uniform
  format, distinct formats joined as "CD/DVD", or omitted when unknown.
  (`identify/musicbrainz.py`, `tests/test_infer_release_type.py`)

### Fixed (ingest resilience — 2026-07-12)
- **A flaky Cover Art Archive fetch crashed the whole ingest/apply job** — when the archive.org
  mirror CAA redirects to answered with a 500 or failed TLS verification, the exception escaped
  the best-effort cover step and aborted the pipeline, so the file was never tagged or moved (a
  large fraction of a bulk import failed this way, each failure retriable). A CAA *fetch failure*
  (5xx/SSL/connection) now parks the job in `needs_review` with a new `cover_fetch_failed` reason —
  bailing before the destructive write/move so the source file is untouched and the review "Apply"
  path can retry the fetch. A genuine "no art in CAA" (HTTP 404) is unchanged: the job completes
  art-less. (`ingest/pipeline.py`, `models.py`, `web/templates/docs.html`)

### Fixed (web UI UX sweep — 2026-07-11)
- **Identify phase held the SQLite write lock for its entire network-bound duration** — the
  pipeline `flush()`ed the job's clue log before the MusicBrainz/AcoustID calls, which issues the
  UPDATE and takes the write lock without releasing it; on a slow/dead network every other writer
  (watcher enqueues, any POST from the UI — adding a schedule, saving settings) blocked for the
  busy-timeout and then failed with "database is locked". Now commits before going to the
  network. (`ingest/pipeline.py`)
- **A failed watcher enqueue silently stranded the file in /drop** — the path was already removed
  from the pending map when `enqueue` raised (e.g. the locked-DB case above), so the file was
  never retried until a restart; it is now re-registered for the next settle pass.
  (`ingest/watcher.py`)
- **Toasts (including validation errors) were silently lost on every plain-form submit** —
  `_toast_response` only carried the toast in an `HX-Trigger` header on a 303 redirect, which the
  browser follows itself for regular `<form method=post>` submits, so e.g. "Invalid cron
  expression" on /schedule produced a feedback-free page reload; the toast is now also encoded
  into `dt_toast`/`dt_level`/`dt_job` query params which base.html shows once and strips from the
  URL. (`main.py`, `web/templates/base.html`)
- **Every page scrolled horizontally on narrow screens** — the top nav was a fixed non-wrapping
  row (~690px), and wide tables (queue, schedule, docs, changes) stretched the body; the nav link
  row now scrolls within itself and tables scroll inside their own container under 640px.
  (`web/templates/base.html`, `frontend/app.input.css`, rebuilt `app.css`)
- **Manual MusicBrainz search reported "No results." when the search actually failed** — network
  errors were swallowed into an empty candidate list; the review-page and track-modal searches
  now surface "MusicBrainz search failed — network error" instead, and skip the outer retry layer
  (musicbrainzngs already retries 8× internally) so the failure surfaces sooner.
  (`identify/musicbrainz.py`, `main.py`, `web/templates/_mb_search_results.html`,
  `_track_mb_results.html`)
- **Failed login cleared the username field** — the value is now re-rendered on the 401 response.
  (`main.py`, `web/templates/login.html`)
- **Setup wizard referenced the pre-rename `AIO_USERNAME` env var** — now `DRAGONTAG_USERNAME`.
  (`web/templates/setup.html`)

### Fixed (repo bug sweep — 2026-07-10)
- **Cancelling disc-folder / filename cleanup discarded Track.path updates for files already
  moved** — `fix_disc_folders` and `normalize_filenames` held every path update in one session
  and committed once after the loop, so a Stop request (or an unguarded exception) mid-run rolled
  the DB back to paths that no longer exist on disk; both now commit per physical move/rename,
  matching the organizer and the album-consistency fixer. (`library/actions.py`)
- **Apply-match was an unauditable destructive rewrite that wiped embedded lyrics** — the
  per-track "apply MB match" route ran the full canonical `write_tags` (which clears every
  existing tag) without capturing a snapshot or recording a `FileChange`, so the rewrite was
  invisible in /changes and unrevertable — and since it fetches no lyrics, the file's embedded
  lyrics/advisory were destroyed and the dashboard counters reset. It now snapshots first,
  records an audit row (`job_id` null — no pipeline job backs it), and carries the file's own
  lyrics/advisory across the rewrite. (`main.py`)
- **Review-applied files could be written with no RELEASETYPE and skipped smart formatting** —
  the single- and bulk-apply review handlers call `_commit_tag_path` directly, bypassing
  `_finalize_and_commit`'s RELEASETYPE inference, `RELEASESTATUS=Official` default, and
  formatting pass; those guarantees now live in `pipeline.prepare_tags`, shared by the pipeline
  and all three manual apply paths (an explicit `release_type_override` still wins, and the
  dry-run gate deliberately stays pipeline-only). (`ingest/pipeline.py`, `main.py`)
- **Re-tagging an in-library file that moved left a phantom Track row at the old path** —
  `_upsert_track` only looked up the destination path, so a requeue/bulk re-tag whose canonical
  destination changed inserted a duplicate row and orphaned the old one (double-counted in the
  library and dashboard, `protected` flag lost) until the next scan pruned it; the row at the
  pre-move path is now re-pointed instead. (`ingest/pipeline.py`)
- **Linking an album onto an M4A could destroy its track/disc totals** — the MP4 branch of
  `write_album_link_tags` gated totals with `is not None`, so a representative track carrying a
  total of 0 ("unknown", per convention) overwrote the file's existing `trkn`/`disk` total half
  with 0; now uses truthiness like the FLAC/ID3 branches. (`tagging/partial.py`)
- **Move-back orphaned the lyric sidecar** — the pipeline moves a track's `.lrc` into the
  library beside the audio, but `move_back` returned only the audio file to its original folder,
  leaving the sidecar next to a file that no longer exists; the sidecar now follows the audio
  (both on the move and on a DB-failure rollback). (`library/revert.py`)
- **Scheduled batches ran against stale track data** — the route layer unconditionally prepends
  a library scan to every batch ("so it never runs against stale track data") but the scheduler's
  `batch_organize`/`batch_retag` dispatch built its chains without one, so a cron-fired organize
  moved files based on whatever the Track table last saw; both now scan the target folder first.
  (`scheduler.py`)
- **Scan pruning left Job rows pointing at deleted tracks** — `_prune_missing` deleted Track
  rows directly while the manual delete route carefully nulls referencing `Job.track_id`; the
  scanner now detaches jobs the same way. (`library/scanner.py`)
- **The in-app manual documented a review reason that can no longer occur** —
  `missing_releasetype` was removed when RELEASETYPE inference was added, but /docs still listed
  it (and a comment in `musicbrainz.py` still claimed absence routes to review). (`docs.html`,
  `identify/musicbrainz.py`)

### Fixed (repo bug sweep — 2026-07-09)
- **Explicit `"artist": null` in an MB credit crashed candidate scoring** — `score_candidate`
  still used `credits[0].get("artist", {})`, which passes a stored `None` through to
  `.get("name")` and errors the whole job; now guarded with `or {}` like the four
  `musicbrainz.py` credit helpers. (`identify/scoring.py`)
- **Conflict-blocked ingests were unrevertable** — `_commit_tag_path` rewrites the file's tags
  *before* the move, but on a destination conflict it returned without persisting the captured
  snapshot, so /changes showed nothing and the destructive write could never be undone. The
  conflict branch now records a `FileChange` at the file's real (unmoved) location, and
  `resolve_conflict` re-points that row at the final destination after a replace/rename so
  revert and move-back keep working. (`ingest/pipeline.py`, `main.py`)
- **`resolve_conflict` was an unlocked fourth file mutator with silent failures** — it moved
  files without `filelock.path_lock` (racing the ingest worker / organizer / revert on the same
  path), had no `needs_review` status guard (a stale double-submit re-ran the move after the
  source was gone → raw 500), and a `moved=False` result fell through to a success-looking
  redirect. Now: status guard with an error toast, the move + lyric-sidecar move run under
  `path_lock(src)`, and a failed move reports the file's true location. (`main.py`)
- **Library actions and per-track edit routes mutated files without `path_lock`** — the
  album-consistency fixer (tag patch + move), disc-folder flattening, filename normalization,
  and the in-place writers (fetch lyrics/covers, advisory re-tag, manual tag edit, link-album,
  apply-match, single-track lyrics) all violated the "every mutator holds the per-path lock"
  invariant; each per-file mutate/move section now takes `filelock.path_lock`.
  (`library/actions.py`, `main.py`)
- **"Re-tag selected" silently skipped needs-review tracks** — the route called
  `pipeline.enqueue` without `requeue_reviews=True`, so the dedup hit returned the stuck
  `needs_review` job, `process()` refused it, and the toast still claimed "Queued N track(s)";
  it now resets stuck reviews to queued like the bulk/batch callers. (`main.py`)
- **Revert left the Track row half-stale** — `_refresh_track` re-synced titles and MB ids but
  not track/disc numbering, advisory, or lyrics presence, so the organizer computed
  destinations from pre-revert numbering and the dashboard counters drifted; it now refreshes
  every field the snapshot restore can change. (`library/revert.py`)
- **`GET /jobs/{id}/log` rendered the log as raw HTML** — the response is built outside Jinja's
  autoescape and job logs embed MusicBrainz-sourced metadata and tracebacks, so markup in a
  title was interpreted, not displayed; the text is now `html.escape`d. (`main.py`)
- **Incomplete-albums pagination dropped the search filter** — the prev/next hrefs interpolated
  `q` without `| urlencode`, so an `&`/`#`/`+` in the query (e.g. "R&B") silently cleared the
  filter when paging. (`library_incomplete.html`)
- **Schedule page claimed cron times were UTC** — expressions are deliberately interpreted in
  the display timezone (`scheduler._cron_tz`), so the copy and statusbar told users the wrong
  firing time whenever TZ was set; both now show the resolved zone name. (`main.py`,
  `schedule.html`)
- **One failed upload stream aborted the whole batch** — a mid-stream read error in
  `save_uploads` raised out of the loop (500) instead of the documented skip-and-continue; the
  partial file is still unlinked, the error is collected, and the remaining files upload.
  (`ingest/uploads.py`)
- **`POST /library/organize` bypassed the batch guard** — unlike every batch route, two quick
  organize clicks started two concurrent file-moving tasks; the route now refuses while another
  background task is running. (`main.py`)
- **Watcher toggle leaked a settle thread per cycle** — `watcher.stop()` stopped the observer
  but the settle loop ran forever, and each re-enable spawned a fresh one; the handler now
  carries a stop event that `stop()` sets. (`ingest/watcher.py`)
- **`library/actions.py` annotated with `Any` without importing it** — harmless under
  PEP 563 but a latent `NameError` for any `get_type_hints()` caller. (`library/actions.py`)

### Changed (UI polish — 2026-07-09)
- **Dashboard banner subtitle removed** — the `« identify · tag · organize »` line is gone; the
  closing rule now sits one blank line under the wordmark. (`dashboard.html`)

### Fixed (statusbar hotkeys + login corner — 2026-07-09)
- **Statusbar key chips are now clickable everywhere** — clicking a `.dt-key` chip synthesizes
  the keydown it advertises (multi-char labels `space`/`enter`/`esc` map to their key names, a
  `⌥` prefix sets `altKey`), so the mouse path and keyboard path share one code path; chips get
  `cursor-pointer` + hover styling. (`base.html`, `frontend/app.input.css`, `app.css`)
- **Every advertised hotkey is actually wired** — dashboard `u` (open file picker), `r` (focus
  the folder-tag input), `j` (jobs) were dead and its `/` chip had no search target (chip
  removed); schedule `n`/`space`/`enter` now act on the new-task form and the hovered row
  (library's hover-focus pattern); `esc` works on the manage-libraries and incomplete-albums
  pages; incomplete-albums `/` focuses its search box; docs gained a working section filter for
  `/` and its mislabeled `g api-docs` chip became `a api-docs` with a real binding.
  (`dashboard.html`, `schedule.html`, `library_folders.html`, `library_incomplete.html`,
  `docs.html`)
- **Login panel's top-right corner reticle sat 16px below the corner** — the form's `space-y-4`
  adds `margin-top` to every child after the first, and margins offset absolutely-positioned
  boxes, so the only top-anchored non-first reticle drooped onto the right border. All four
  reticles now carry `!mt-0`. (`login.html`)

### Added
- **Link a track to an existing library album** from the manual edit form — a searchable album
  picker writes album, album artist, disc/track totals, and MusicBrainz album IDs from an
  already-indexed album onto the track via the new `tagging.partial.write_album_link_tags()`
  helper and `POST /library/tracks/{id}/link-album` route. No MusicBrainz network call; title,
  artist, and track number are left untouched. (`tagging/partial.py`, `main.py`,
  `_track_edit_modal.html`)
- **Global keyboard shortcuts**, wired on every page — a `dtKeys` registry in `base.html`
  (`/` focus search, `?` key reference overlay, `g`+letter to navigate, plus
  `dtKeys.register(key, fn)` for page-specific bindings) with matching bindings added to
  `library.html`, `changes.html`, `queue.html`, `job_detail.html`, and `settings.html` so every
  key advertised in a page's status bar actually does something.
- **Display timezone setting** — `UserSettings.timezone`, resolved as Docker `TZ` (locked, always
  wins) → in-app override → UTC. Settings UI shows the active value and locks the field when `TZ`
  is set on the container. (`config.py`, `main.py::_local_tz/settings_page/settings_update`,
  `settings.html`)
- **New dragon-themed favicon** (`favicon.svg`) and a grander two-tier ASCII dragon/wordmark
  banner on the dashboard, also echoed to the startup log.

### Changed
- **Edit-modal header reorganized** — "fetch lyrics" and "protect" moved into a compact top-right
  button cluster next to the close button; their explanatory paragraphs became `title=` hover
  text. Both forms' actions are unchanged. (`_track_edit_modal.html`)
- **Settings tooltips** switched from a hover-only `tip()` macro to an inline `hint(text)` macro
  that renders a muted line directly under each field, including the skip-fields grid (no more
  hover-only `title=` tooltips anywhere on the page). (`settings.html`)
- **Filename-template live preview** now parses real `str.format()` tokens (including
  `{track:02d}`-style zero-padding specs) instead of naive string substitution, so the preview
  matches `library/paths.render_filename` exactly. (`settings.html`)
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

### Changed (dashboard banner — 2026-07-08)
- **Dashboard banner rebuilt** — the ASCII dragon is removed and the wordmark replaced with an
  ornate larry3d-style `DRAGONTAG` with a decorative frame and an
  `« identify · tag · organize »` subtitle. The art is sized with an inline
  `font-size: clamp(...)` so its 75 monospace columns always fit the viewport — which also kills
  the horizontal scrollbar (the previous banner's `text-[6px]`/`sm:text-base`/`md:text-lg`
  classes were never compiled into `app.css`, so it rendered at 16px and overflowed its
  `overflow-x-auto` container). Pure ASCII/Latin-1 on purpose: the vendored woff2 subsets don't
  cover block/box-drawing glyphs, and a fallback-font glyph would break the column grid. The
  startup-log banner uses the same lettering. (`dashboard.html`, `main.py`)

### Fixed (core/library/web bug sweep — 2026-07-08)
- **Organize ran without the per-path file lock** — `organize_folder` moved files while the
  ingest worker or a revert could be mid-write on the same path; each track's move + DB update
  is now serialized under `filelock.path_lock`. (`library/organizer.py`)
- **Failed rollback moves were reported as success** — `move(..., overwrite=False)` returns a
  conflict result instead of raising, so when the original path was re-occupied, the organizer
  logged "rolled back" and `move_back` claimed "file restored" while the file actually stayed at
  the new location. Both now check `MoveResult.moved` and report DIVERGED / "could not be
  restored" accurately. (`library/organizer.py`, `library/revert.py`)
- **Slow-but-alive tasks were reaped to `error`** — a task in one long non-heartbeating step
  (e.g. zipping a large backup) never bumps `updated_at`, so `reap_stale_jobs` killed it at the
  15-minute mark, broke its Stop button, and raced its completion write. The reaper now skips
  jobs whose worker thread is still alive. (`tasks.py`)
- **Cron schedules fired in UTC while the UI described them unqualified** — `0 6 * * *` now
  means 6 AM in the display timezone (Docker `TZ` → in-app setting → UTC), converted to naive
  UTC for storage/compare. (`scheduler.py`)
- **Webhook notify could raise into the pipeline thread** — payload construction (settings load,
  tag attribute access) ran outside the fire-and-forget guard; the whole body is now wrapped, so
  errors are logged, never raised, as the module contract states. (`notify.py`)
- **Artist-credit with an explicit `"artist": null` crashed tag assembly** — `_credit_phrase`
  used `c.get("artist", {})`, which doesn't guard an explicit `None`; now matches the sibling
  helpers' `(c.get("artist") or {})`. (`identify/musicbrainz.py`)
- **Failed upload streams left truncated files in the drop folder** — a mid-stream disconnect
  now unlinks the partial file instead of letting the watcher ingest a corrupt track.
  (`ingest/uploads.py`)
- **`TRACKTOTAL`/`DISCTOTAL` of `0` written as a literal "0"** — a zero total means "unknown"
  (matching the `NN/TT` logic and the MP4 writer) and is no longer written. (`tagging/schema.py`)
- **Search queries corrupted sort/pagination links** — `q` was interpolated into hrefs without
  percent-encoding, so `rock & roll` or `drum#bass` silently truncated the filter when sorting
  or paging; now piped through `urlencode`. Also fixed the ragged `3   :05` duration column
  (`%-4.0f` left-justify). (`library.html`, `_library_tracks.html`)
- **Job-log cap measured characters, not bytes** — non-ASCII logs could grow the row to ~4× the
  intended 256 KiB ceiling; truncation now operates on encoded bytes and accounts for the
  marker. (`models.py`)
- **Schema migration ALTERs shared one transaction** — contrary to the comment, a failed ALTER
  could skip the rest on non-SQLite backends; each now runs in its own transaction. (`db.py`)
- **Corrupted `log_verbosity` setting crashed `logsetup.apply`** — non-numeric values now fall
  back to INFO. (`logsetup.py`)
- **Dead `ID3NoHeaderError` branch removed** — mutagen never raises it from `MP3()`; the branch
  would also have double-added tags if reached. (`tagging/writers/mp3.py`)

### Fixed (tagging-pipeline bug sweep — 2026-07-08)
- **Dry-run bypass in the MBID short-circuit (silent library rewrite)** — files identified via
  existing `MUSICBRAINZ_TRACKID`/`ALBUMID` skipped the dry-run gate *and* the finalize step, so a
  dry-run bulk re-tag of an already-tagged library actually rewrote and moved every file, without
  RELEASETYPE inference, `RELEASESTATUS` defaulting, or smart formatting. Both paths now share
  `_finalize_and_commit`. (`ingest/pipeline.py`)
- **MB ids were unreadable on MP3/WAV/MP4** — the reader queried bare `MUSICBRAINZ_*` keys, but
  ID3 stores them as `TXXX:…`/`UFID:http://musicbrainz.org` and MP4 as `----:com.apple.iTunes:…`,
  so the MBID short-circuit only ever worked for FLAC. Added the prefixed aliases (both dragontag
  and Picard-style descs), UFID payload and MP4-freeform bytes decoding. (`identify/existing_tags.py`)
- **MP4 quick-edit destroyed track/disc totals** — `write_basic_tags` wrote `trkn=(track, 0)`
  when only the number was edited; the untouched half of the tuple is now preserved. Blanked
  fields in the track-edit modal are also now *cleared on the file* (new opt-in `clear_blanks`
  mode), so a cleared field no longer resurrects from disk on the next scan. (`tagging/partial.py`,
  `main.py`)
- **MP4 revert snapshots silently came back empty** — a gapless/podcast bool atom (`pgap`/`pcst`)
  made `_capture_mp4` raise mid-iteration, which `capture()` swallowed into an empty snapshot
  (revert then did nothing); int atoms (`tmpo`, `stik`, …) restored as strings made mutagen raise.
  Both atom families are now handled explicitly in both directions. (`tagging/snapshot.py`)
- **Backup restore could destroy the live DB across filesystems** — the staging dir lives in the
  system temp, so the staging→`/config` `os.replace` raised `EXDEV` on the normal Docker volume
  layout *after* the live `dragontag.db` had been renamed to `.pre-restore`, and cleanup deleted
  the staged copy. Files are now re-staged inside the config dir first (`shutil.move` bridges the
  FS boundary) so every swap is a same-FS atomic rename. (`backup.py`)
- **A failed staged replace deleted the incoming file** — after `shutil.move(source→tmp)`, a
  verification failure unlinked the temp, which *was* the only copy of the source; it is now
  moved back (or left as a recoverable orphan). (`library/mover.py`)
- **Filename parser truncated numeric titles** — `7 Years.flac` parsed as "Years",
  `99 Luftballons.flac` as "Luftballons". A bare-space separator is now only trusted after a
  zero-padded number (`01 Title.flac` still works); punctuation separators are unchanged.
  (`identify/filename_parse.py`)
- **`split_multi_artist` left a trailing bracket** — `"A (feat. B)"` split to `["A", "B)"]`; the
  unmatched closer is now trimmed (balanced brackets in names are kept). (`identify/artist_split.py`)
- **Album-consistency checker could invent a metadata state** — album and album-artist were
  majority-voted independently, so anti-correlated groups normalized to an `(album, artist)` pair
  no track ever had; the pair is now voted jointly. DB updates also commit per track, so an
  exception mid-run can no longer roll back rows for files already physically moved.
  (`library/actions.py`)
- **`fix_disc_folders` broke on `{disctotal}` templates** — the rename `format()` call omitted the
  placeholder that `build_destination` supplies, so such templates KeyError'd every rename into
  the silent error counter. (`library/actions.py`)
- **Review/conflict routes hardened** — re-applying an already-resolved review job no longer flips
  a `done` job to `error` (missing `needs_review` guard); resolving a destination conflict now
  moves the `.lrc` sidecar and indexes the moved file as a Track row (replace also refreshes the
  overwritten path's stale row); a bad MB id in the track-edit "apply match" returns a toast
  instead of a 500. (`main.py`)
- **Bulk re-tag no longer silently skips `needs_review` files** — the enqueue dedup returned the
  stuck job and counted it as queued while the worker refused to process it; explicit bulk/batch
  re-tags now reset those jobs to `queued` (`enqueue(requeue_reviews=True)`). The watcher keeps
  the protective dedup. (`ingest/pipeline.py`, `ingest/bulk.py`)
- **Settings hardened** — pipeline-critical values are validated at save time: filename/disc-folder
  templates are test-rendered (a `{name}` typo used to fail every subsequent ingest at the move
  step), `score_threshold` is bounded to [0,1], timeouts must be positive, and an invalid value
  returns an error toast instead of an unhandled 500. The **watcher toggle now takes effect
  immediately** (start/stop on save) instead of after a restart, and **Run now** respects the
  same-kind-running guard the scheduler tick enforces. (`config.py`, `main.py`)
- **Scanner prunes deleted files** — Track rows whose file vanished from disk are removed after a
  scan instead of lingering forever (phantom dashboard counts, spurious organizer "missing"
  errors). (`library/scanner.py`)
- **Multidisc filename/folder parity** — with `disc_total > 1` but no disc number,
  `render_filename` chose the multidisc template (constant `{disc}`→1) while `build_destination`
  skipped the `Disc N` folder, producing colliding names; both now use the same condition.
  (`library/paths.py`)
- **Cover-art overwrite gate honors its contract** — a fetched cover that cleared the static
  pixel floor could still clobber a *larger* hand-curated `cover.jpg`; the existing cover's width
  is now compared too (explicit user-chosen art still always wins). (`library/mover.py`)
- **Smaller correctness fixes** — genre dedup is hyphen/space-insensitive (`Hip Hop`/`Hip-Hop` no
  longer both survive); the grammar punct-spacing rule no longer explodes initialisms
  (`R.E.M.` → `R. E. M.`); MP4 `rtng` is written on the iTunes scale (clean = 2, not 0, so Apple
  players show the Clean badge) and legacy `rtng=4` reads as explicit; link-album removes the full
  writers' underscore-style MB-id frames before writing Picard-style ones (no duplicate
  conflicting ids); `fetch_bytes` defaults to `allow_redirects=False` so the SSRF guard can't be
  bounced past by a 30x (trusted CAA/LRCLIB fetches opt in); the fpcalc parser skips
  informational stdout lines instead of discarding a good fingerprint. (`identify/genres.py`,
  `tagging/formatter.py`, `tagging/writers/mp4.py`, `tagging/partial.py`,
  `identify/existing_tags.py`, `net.py`, `tagging/coverart.py`, `tagging/lyrics_fetcher.py`,
  `identify/acoustid.py`)

### Tests (2026-07-08)
- New suites covering every fix above: `test_pipeline_dry_run_shortcircuit.py`,
  `test_existing_tags_mbid_readback.py`, `test_partial_clear_and_mp4_totals.py`,
  `test_snapshot_mp4_atoms.py`, `test_backup_restore_crossfs.py`,
  `test_mover_staged_source_preserved.py`, `test_filename_parse_titles.py`,
  `test_artist_split_brackets.py`, `test_album_consistency_pair_vote.py`,
  `test_disc_folder_template.py`, `test_routes_sweep_guards.py`,
  `test_resolve_conflict_indexes_track.py`, `test_scanner_prune.py`,
  `test_paths_multidisc_parity.py`, `test_cover_overwrite_gate.py`,
  `test_config_validators.py`, `test_genre_dedup.py`, `test_formatter_initialisms.py`,
  `test_advisory_rtng.py`. Suite: 291 passing.

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
