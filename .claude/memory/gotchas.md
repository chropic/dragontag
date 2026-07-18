---
name: gotchas
description: Bug patterns actually found and fixed in this codebase. Read before writing file-moving, tag-writing, threading, timezone, or template code — these WILL recur if forgotten.
metadata:
  type: project
---

# Gotchas — distilled from four full bug sweeps

PR #39 (2026-07-08) fixed 28 bugs in the tagging pipeline; PR #40 (same day) fixed 15 more in
core/library/web; the 2026-07-09 repo sweep fixed 14 more, and the 2026-07-10 sweep 9 more
(several of them re-occurrences of the patterns below). The *patterns* are what keeps
recurring. When writing new code in one of these areas, check the pattern first.

## File moves & rollbacks

- **`library/mover.move(..., overwrite=False)` does not raise on conflict.** It returns
  `MoveResult(moved: bool, destination: Path, conflict: bool)`. Wrapping it in try/except and
  assuming an exception means failure produced two shipped bugs where a failed rollback was
  reported as success ("rolled back" / "file restored") while the file sat elsewhere. **Always
  branch on the returned result.** When a rollback fails, log CRITICAL "DIVERGED" and tell the
  user the true file location — never claim recovery that didn't happen.
- **Every mutator of a physical file must hold `filelock.path_lock(path)`.** The organizer ran
  for months without it and could interleave with the ingest worker or a revert on the same
  file — and the 2026-07-09 sweep found six more unlocked mutators that had accreted since
  (`resolve_conflict`, the album-consistency fixer, disc-folder flatten, filename normalize,
  the fetch-lyrics/covers/advisory actions, and the per-track edit routes in `main.py`).
  Known mutators now: pipeline `_commit_tag_path`, revert/move-back, organizer,
  `resolve_conflict`, every file-touching function in `library/actions.py` (including
  `fix_genres_for_folder`'s `write_genre`), and the per-track edit/link/apply/lyrics routes. **Any new rename/retag/move feature joins this list — audit
  for the lock in review, it is the single most re-occurring bug class in this repo.**
- **A destructive tag write and its audit row must not be separable.** `_commit_tag_path`
  rewrote tags *before* the move; the destination-conflict branch returned without recording a
  `FileChange`, so the write was invisible in /changes and unrevertable. Any early-exit added
  after a write must still persist the snapshot (and anything that later moves the file must
  re-point the audit row's `file_path`, as `resolve_conflict` now does).
- Files moved on disk before the DB commit: if the commit then fails you MUST move the file
  back (and verify the move-back worked). `Track.path` is the only record of where a file lives.
- **Loops that move files must commit `Track.path` per move, never once after the loop.** A
  Stop request (`ctx.check_cancelled` raising `TaskCancelled`) or any unguarded exception exits
  the `with session()` block before an end-of-loop commit, silently rolling back the path
  updates for files already physically moved. Bit `fix_disc_folders` and `normalize_filenames`
  (2026-07-10); the organizer and album-consistency fixer already committed per track for this
  reason — copy them.
- **Anything that moves an audio file also moves its `.lrc` sidecar** (`mover.move_lyric_sidecar`).
  `move_back` forgot; note the disc-folder flatten does NOT need an explicit call because its
  loop already iterates every file in the disc dir.
- **`cleanup_library` must exclude its own quarantine root from every walk and from the scanner,
  or it re-ingests its own trash.** Its `os.walk`/`iterdir` passes skip `qroot` (and any
  `.dragontag-trash` dir), and on the first quarantine it appends `qroot` to
  `settings().scan_exclude_dirs`. It also never quarantines audio (assert on `SUPPORTED_EXTS`) and
  guards a missing/unmounted library root before `iterdir()` (raises `FileNotFoundError`
  otherwise). Twin-merge uses bespoke moves preserving each file's relative sub-path — NOT
  `_normalize_track_to_pair`, which recomputes the destination via `build_destination` and would
  re-fold/re-template the very folders being consolidated.
- **Schema guarantees for manual apply paths live in `pipeline.prepare_tags`.** Any route that
  calls `_commit_tag_path` directly (review apply, bulk apply, apply-match) must call
  `prepare_tags` first or files get written without RELEASETYPE (the one mandatory field),
  without the `RELEASESTATUS=Official` default, and without the smart-formatting pass. The
  dry-run gate stays in `_finalize_and_commit` only — an explicit user apply must not re-enter it.
- **A full `write_tags` outside the pipeline is still a destructive write**: snapshot first,
  record a `FileChange` (nullable `job_id` is fine), and carry the file's embedded
  lyrics/advisory across the canonical clear if you aren't re-fetching them — `assemble_tags`
  brings no lyrics, so a bare rewrite silently deletes them (bit apply-match, 2026-07-10).
- **`_upsert_track` needs the pre-move path** (`original_path=`) when the file moved, or a
  re-tag that changes the canonical destination leaves a phantom Track row (and loses
  `protected`) at the old path until the next scan prunes it.
- **Case-only directory rename must gate the two-step temp dance on `os.path.samefile`,
  not on `str(a).lower() == str(b).lower()`.** (Historical: `_rename_artist_dir` was removed
  in the 2026-07 scope cut — dragontag no longer renames directories just to change case —
  but the pattern stands for any future rename code.) On a case-insensitive mount the target
  "exists" (same inode) so a direct rename is a no-op and you must go through a temp name;
  on case-sensitive ext4 two genuinely distinct dirs can share a case-insensitive name and
  the temp dance strands files. Only do the two-step when `dst.exists()` **and**
  `os.path.samefile(src, dst)`; otherwise refuse and merge per-file instead.
- **Destination-directory resolution must be fail-closed and atomic (fixed class, 2026-07-16).**
  Two bugs in `paths._reuse_folded_dir`/`build_destination` minted case-twin artist dirs
  (`fakemink`/`Fakemink`) that became phantom files on a case-insensitive SMB view of the
  case-sensitive library volume: (a) TOCTOU — the scandir-based case-insensitive reuse check
  and the later `mkdir` were not atomic, and `path_lock` keys on the FILE path so same-artist
  files in different threads never serialize; (b) fail-open — `except OSError: pass` around
  the scan meant one transient share error ⇒ "no sibling exists" ⇒ twin created. Fixes:
  resolve+mkdir under the module-global `_dir_resolve_lock` (`build_destination(...,
  ensure_dirs=True)`), and an OSError scanning an EXISTING parent raises
  `DestinationUnresolved` — pipeline routes to review (`destination_unresolved`, recording
  the in-place write's FileChange), organizer skips. Never "pretend and create" on a scan
  failure, and never rely on `path_lock` to serialize directory creation.

## mutagen / tag-writing traps

- `MP3(path, ID3=ID3)` **never raises `ID3NoHeaderError`** — mutagen swallows it and leaves
  `.tags is None`. The correct pattern is `if audio.tags is None: audio.add_tags()`. A dead
  except-branch for it would double-`add_tags` (which raises on tagged files) if ever reached.
- MP4 bool atoms (`pgap`, `pcst`) and int atoms (`tmpo`, `stik`, …) need explicit handling in
  snapshot capture/restore — restoring ints as strings makes mutagen raise; an unexpected atom
  type mid-iteration used to silently produce an *empty* snapshot (revert became a no-op).
- ID3 stores MB ids as `TXXX:MusicBrainz ...`/`UFID:http://musicbrainz.org`, MP4 as
  `----:com.apple.iTunes:...` freeform (bytes — decode!). Reading bare `MUSICBRAINZ_*` keys only
  works on FLAC.
- `trkn`/`disk` MP4 tuples: writing `(track, 0)` destroys the total half. Preserve the existing
  tuple element you're not editing. Track/disc **total of 0 means "unknown"** — never write a
  literal `TRACKTOTAL=0` (use truthiness, not `is not None`).
- A "clear this field" edit must actually delete the frame/atom, or the value resurrects from
  disk on the next scan (`clear_blanks` mode in `tagging/partial.py`).

## Dict-parsing traps (MusicBrainz payloads)

- `d.get(key, {})` does **not** protect against an explicit `None` value — `{"artist": None}`
  returns `None`, not `{}`. Use `(d.get("artist") or {}).get("name")`. This exact bug appeared
  in `_credit_phrase` after being fixed in its three sibling helpers; grep for `get("artist", {})`
  when touching MB parsing.
- MB artist-credit lists mix strings and dicts; join phrases live on the credit entry. Any new
  credit-walking code should mirror `_credit_names`.
- **MB per-medium fields (`format`, `track-count`) are NOT album-level.** Writing them per-track
  splits albums in players: a mixed CD+DVD release got differing `MEDIA`, and RELEASETYPE
  inference from the per-disc `track_total` tagged a short disc 2 as "EP" while disc 1 said
  "Album". Normalize release-wide before writing anything players group on
  (`_release_media` / `_release_track_total` in `identify/musicbrainz.py`,
  `TrackTags.release_track_total`).
- **The release-group genre fallback needs a dedicated `fetch_release_group`.** Genre comes from
  community `tag-list` folksonomy tags, tried on the recording then the release-group. But a
  release-group *nested* in a release response never carries a `tag-list` (a release's `tags`
  include attaches release-*level* tags), so `rel["release-group"].get("tag-list")` is always
  empty — the fallback was dead code and untagged recordings shipped with no genre. `assemble_tags`
  now fetches RG tags explicitly, and falls back when the recording *derives* to nothing (via
  `derive_genres`), not merely when its raw tag-list is absent, so junk-only recording tags still
  reach the release-group.
- **Per-file identification must not pick releases in isolation.** Near-tied candidates (within
  `_CONSENSUS_EPSILON`) of one release group scattered an album's tracks over several editions
  (different `MUSICBRAINZ_ALBUMID`/DATE/RELEASESTATUS/MEDIA ⇒ split albums — ~18% of a real
  4,000-file library). The real fix (2026-07-16) is **album-first identification**: jobs from
  one album folder share `Job.group_key` and `ingest/album.py` elects ONE release for the
  whole group; the per-file MBID short-circuit is demoted to a weighted candidate inside a
  group, because trusting a file's existing (possibly drifted) album id is exactly what locked
  splits in. `_select_candidate`'s consensus preference remains the per-track safety net for
  loose singles. A member not on the elected release goes to review (`album_mismatch`), never
  silently forced onto the album.

## Threading / background tasks

- The reaper (`tasks.reap_stale_jobs`) must not kill quiet-but-alive tasks: `updated_at` only
  advances when a task calls `ctx.progress()/log()`. A 20-minute zip with no heartbeat is
  healthy. Liveness is tracked in `tasks._live_threads` — keep it updated if you change how
  worker threads are spawned.
- "Fire-and-forget" helpers (`notify.py`) must wrap **payload construction** too, not just the
  network call — attribute access on a malformed `tags` object in the caller thread raised into
  the pipeline's job-completion path.
- Popping a job's cancel event (`tasks._cancel_events`) breaks the Stop button — only do it when
  the job is truly finished.
- Never poll/sleep in a request handler; long work goes through `tasks.run_task`.
- **Never `flush()` (or otherwise hold an open write transaction) across a network call.**
  SQLite has one writer: `pipeline._process_inner` flushed the clue log before the
  MusicBrainz/AcoustID calls and blocked every other writer (watcher enqueues, UI POSTs)
  with "database is locked" for the whole identify phase. Commit before going to the network.
- The drop watcher must re-register a path if `enqueue` raises, or the file is silently
  stranded until restart (`watcher.settle_loop`).

## Timezone & time

- Everything stored is **naive UTC** (`timeutil.now_utc()`). Mixing in `datetime.now()` or
  aware datetimes breaks comparisons silently.
- Cron expressions are interpreted in the display timezone (`scheduler._cron_tz`: `TZ` env →
  `settings().timezone` → UTC) and converted back to naive UTC. If you show a time to the user,
  it must match when the thing actually happens — the old code showed "At 06:00 AM" and fired at
  06:00 *UTC*.

## Web layer

- Jinja autoescape protects HTML contexts but **not URL contexts** — interpolating a user string
  into an `href` query needs `| urlencode` or `&`/`#`/`+` in a search query corrupts the link
  (filter silently dropped when sorting/paging). Check every `href="...{{ q }}..."` — this
  recurred in `library_incomplete.html` a day after being fixed in `_library_tracks.html`.
- **Responses built outside Jinja get no autoescape.** `GET /jobs/{id}/log` f-string-built its
  `<pre>` around raw `job.log` — which embeds MusicBrainz-sourced titles and tracebacks.
  Any handler returning hand-assembled `HTMLResponse` markup must `html.escape` interpolated
  text.
- printf-style format specs in templates: `%-4.0f` left-justifies (renders `3   :05`); widths in
  a proportional context are almost never what you want.
- Byte caps vs character caps: `len(str)` counts characters; non-ASCII content (ubiquitous in
  music metadata) is up to 4 bytes each. Cap on `len(s.encode("utf-8"))` and slice bytes,
  decoding with `errors="ignore"` (`models.append_job_log` is the worked example).
- Form checkboxes the browser may omit must be `str | None = Form(None)` + `bool(...)`.
- **`_toast_response`'s HX-Trigger header is invisible to plain `<form method=post>` submits**
  (the browser follows the 303 itself). Toasts also travel as `dt_toast`/`dt_level`/`dt_job`
  query params that base.html's toastManager shows on load and strips — keep both channels when
  touching toast plumbing.
- On narrow screens: wide tables scroll inside themselves via the global `@media (max-width:
  640px) table` rule in `frontend/app.input.css`; the nav link row is `flex-1 min-w-0
  overflow-x-auto`. New wide layouts must not widen the body.

## Pipeline semantics

- **Dry-run must gate every path that writes or moves** — the MBID short-circuit once bypassed
  it and a "dry-run" bulk re-tag rewrote the whole library. Any new identify path must funnel
  through `_finalize_and_commit`.
- `acoustid.lookup` swallows all exceptions and returns `[]` — don't add code expecting it to
  raise.
- Scoring weights sum to 1.0 deliberately: a candidate missing album+duration cannot reach the
  0.85 auto-apply threshold. Don't "fix" this.
- **`pipeline.enqueue` dedups on active jobs, and `needs_review` counts as active.** Any
  caller that means "explicitly re-tag this file" must pass `requeue_reviews=True`, or a track
  stuck in review is returned as the "queued" job and then silently refused by `process()`
  (`_PROCESSABLE_STATUSES`). This bit `bulk.enqueue_folder` first and `retag-selected` second.
- Watcher: `_collect_ready` re-sets the pending event when items remain — the clear-before-sleep
  pattern is correct as written; don't refactor it without reading the loop.
- Uploads/stream writes into the watched drop folder must clean up partial files on failure —
  the watcher will happily ingest a truncated file after the settle window.

## Config / settings

- `UserSettings` validators exist because a typo'd filename template used to save fine and then
  fail *every* ingest at the move step. Pipeline-critical settings need save-time validation.
- Settings the UI exposes but code treats as fallback-only (per-tag separators) still need to
  keep working — the settings page is a compatibility surface.
- Corrupt stored values (e.g. non-numeric `log_verbosity`) must degrade, not crash startup.

## Process traps for agents

- Python 3.11 `pip install -e .` fails (requires ≥3.12) with a misleading resolver message —
  build the venv from `python3.12`.
- `app.css` is compiled+committed; template class changes without `bash frontend/build_css.sh`
  look like "CSS mysteriously not applying". (This bit the dashboard banner once: `text-[6px]`
  variants were never compiled in, so the art rendered at 16px and overflowed.)
- The vendored font subsets omit box-drawing glyphs — ASCII-art in templates must stay
  ASCII/Latin-1 or the column grid breaks on fallback glyphs.
- CHANGELOG history below the current WIP block is consolidated — never re-expand it.
- `frontend/build_css.sh` downloads the Tailwind standalone CLI from GitHub releases, which is
  **blocked behind the agent proxy** in remote sessions. When you can't rebuild, restrict template
  edits to utility classes already present in the committed `app.css` (grep it: `\.pl-6{`,
  `6f6f6f`, …) so no regeneration is needed. New arbitrary values (`text-[#6f6f6f]`, `pl-6`) will
  silently not exist until someone rebuilds on an unrestricted network.

## Review queue & tag-reading (2026-07-17)

- **`identify/existing_tags.first()` must guard every `tags.get(k)`.** The alias lists mix
  MP4-style keys (`\xa9nam`) with Vorbis names; mutagen's `VCommentDict.get` raises `ValueError`
  (not "missing → None") on a non-ASCII key, so an unguarded lookup crashed *every* dragged FLAC.
  Both `first()` and `_has_lyrics` now wrap `.get` in try/except — keep it that way for any new
  multi-format lookup.
- **Review-queue selection is client-persisted, not server state.** `queue.html` mirrors the
  checked review job-ids to `localStorage['dt.review.selected']` and restores them on
  `DOMContentLoaded`/`pageshow`, pruning ids no longer on the page. The `space` keybinding and the
  bulk/select-all boxes all funnel through a `change` listener that re-saves. If you add/rename
  review checkboxes, keep `name="job_ids" form="review-bulk-form"` or the persistence misses them.
- **Individual Apply folds into an active multi-selection.** A capture-phase click listener on
  `.review-submit-btn` preempts the button's inline `requestSubmit()`: if any *other* review item
  is checked it adds this one and submits the bulk form instead. The bulk submit handler cancels an
  empty submit client-side (with a `showToast` event) so the old "Nothing to apply" server bounce
  can't wipe the DOM selection. The job screen's back/`esc` targets `/queue` (was `/`).
- **The review apply/skip/manual routes are htmx, and the commit is backgrounded.** `review_apply`
  no longer commits in-request (that blocked ~10s and 303-reloaded the page); it reads any uploaded
  cover bytes, then enqueues `_apply_review_match` via `tasks.run_chain` and returns — `_hx_remove`
  (empty **200** + `HX-Trigger showToast`) for htmx so the card swaps out, or `_toast_response`
  (303) for a plain post. Response contract used across these routes: **200 empty** = htmx swaps the
  `closest .dt-review-item` out; **204** (`_toast`) = htmx does NOT swap (error, card stays); **303**
  = non-htmx fallback. Branch on `_is_htmx(request)`. Because the commit is async, tests must poll
  (see `_wait_captured` / `_wait` in the apply/action suites), not assert synchronously.
- **queue.html apply forms carry BOTH `hx-post` and a plain `action`/`method`** (progressive
  fallback). Hidden `recording_id`/`release_id`/`cover_art_url` (single) and `pick_{id}`/`cover_{id}`
  (bulk) are injected in **`htmx:configRequest`** (`evt.detail.parameters`), NOT a `submit` listener —
  htmx serializes before a submit listener would run. The bulk empty-guard `evt.preventDefault()`s in
  configRequest to cancel the request (keeps the selection); bulk removal is driven by the server's
  `reviewApplied:{ids}` HX-Trigger event, plus a global `htmx:afterSwap` re-prunes the persisted
  selection. `POST /review/{id}/skip` = mark `skipped` (reversible); `POST /review/{id}/manual-apply`
  builds a `TrackTags` from manual fields and runs the same `prepare_tags` + `_commit_tag_path`.
- **`cover_fetch_failed` now has a local fallback.** On a `requests.RequestException` from the CAA,
  `_commit_tag_path` calls `pipeline._find_local_cover(src)` → `coverart.find_local_cover` before
  routing to review: sidecar image in the dir (`cover/folder/front/album.*`), then embedded art in
  the track, then in a sibling audio file from the same directory. Only a true no-local-cover miss
  reaches `needs_review` now, so those items are rare — don't assume the queue is full of them.
  `coverart._probe_image` is the shared decode-bomb/JPEG-PNG normaliser for both the CAA and local
  paths.
- **Manual tagger is multi-value + persists + participates in bulk (Round 3).** The manual panel
  writes native multi-value ARTIST / album_artist by rendering one `<input name="artist">` per row
  with a `+` button that clones another; `review_manual_apply` reads them via
  `form.getlist("artist")` (so the route is `async` and takes no `Form(...)` params for the artist
  fields), and `_apply_manual_match` sets `tags.artists = [...]` and
  `artist_display = settings().separators.ARTIST.join(...)` (same for album_artist). If you add
  another repeatable field, mirror this shape or you'll get a single joined blob instead of one
  Vorbis comment per value. Every manual-panel value (including the row count) is persisted per
  job id under `localStorage['dt.review.manual']`; the panel auto-opens for jobs with saved data
  via `x-init` calling `window.dtManualHasSaved(id)`. The bulk `configRequest` gather emits
  `manual_{id}_title` + `manual_{id}_artist` (repeated) + optionals for each checked card whose
  manual form is populated; `review_bulk_apply` prefers a manual override over any MB pick, so a
  mixed MB+manual batch resolves each card correctly. Extra manual fields (`date`, `release_type`,
  `advisory`, `genres`) live on `TrackTags` and go through `prepare_tags` first — the lyrics step
  in `_commit_tag_path` guards `if tags.advisory is None` so a user's explicit choice sticks.
- **Nested htmx forms need scoped `configRequest` listeners.** The MB-search Search button lives
  *inside* `apply-form-{id}`, so a form-level `htmx:configRequest` listener also fires on the
  nested search GET (`evt.detail.elt` is the button). Always guard
  `if (evt.detail.elt !== form) return;` at the top of a form-level listener or you'll mutate a
  child request's params. The MB search inputs also had to be renamed `mb_title`/`mb_artist`/
  `mb_album`/`mb_mbid` so they don't collide with the apply form's own submitted fields —
  `_api_mb_search_inner` reads those param names.
