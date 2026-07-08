---
name: gotchas
description: Bug patterns actually found and fixed in this codebase. Read before writing file-moving, tag-writing, threading, timezone, or template code — these WILL recur if forgotten.
metadata:
  type: project
---

# Gotchas — distilled from two full bug sweeps

PR #39 (2026-07-08) fixed 28 bugs in the tagging pipeline; PR #40 (same day) fixed 15 more in
core/library/web. The *patterns* below are what keeps recurring. When writing new code in one of
these areas, check the pattern first.

## File moves & rollbacks

- **`library/mover.move(..., overwrite=False)` does not raise on conflict.** It returns
  `MoveResult(moved: bool, destination: Path, conflict: bool)`. Wrapping it in try/except and
  assuming an exception means failure produced two shipped bugs where a failed rollback was
  reported as success ("rolled back" / "file restored") while the file sat elsewhere. **Always
  branch on the returned result.** When a rollback fails, log CRITICAL "DIVERGED" and tell the
  user the true file location — never claim recovery that didn't happen.
- **Every mutator of a physical file must hold `filelock.path_lock(path)`.** The organizer ran
  for months without it and could interleave with the ingest worker or a revert on the same
  file. Known mutators: pipeline `_commit_tag_path`, revert/move-back (both directions,
  including rollbacks), organizer. New rename/retag/move features join this list.
- Files moved on disk before the DB commit: if the commit then fails you MUST move the file
  back (and verify the move-back worked). `Track.path` is the only record of where a file lives.

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
  (filter silently dropped when sorting/paging). Check every `href="...{{ q }}..."`.
- printf-style format specs in templates: `%-4.0f` left-justifies (renders `3   :05`); widths in
  a proportional context are almost never what you want.
- Byte caps vs character caps: `len(str)` counts characters; non-ASCII content (ubiquitous in
  music metadata) is up to 4 bytes each. Cap on `len(s.encode("utf-8"))` and slice bytes,
  decoding with `errors="ignore"` (`models.append_job_log` is the worked example).
- Form checkboxes the browser may omit must be `str | None = Form(None)` + `bool(...)`.

## Pipeline semantics

- **Dry-run must gate every path that writes or moves** — the MBID short-circuit once bypassed
  it and a "dry-run" bulk re-tag rewrote the whole library. Any new identify path must funnel
  through `_finalize_and_commit`.
- `acoustid.lookup` swallows all exceptions and returns `[]` — don't add code expecting it to
  raise.
- Scoring weights sum to 1.0 deliberately: a candidate missing album+duration cannot reach the
  0.85 auto-apply threshold. Don't "fix" this.
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
