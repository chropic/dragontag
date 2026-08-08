# Engineering invariants

These contracts protect audio files, database truth, and user-visible state.
Each section points to canonical code and representative regression tests. Read
the implementation before changing it; the tests demonstrate edge conditions
more precisely than prose can.

## Physical files and auditability

**Mutate atomically and lock the whole operation.** Any in-place audio change
uses `tagging/writers/_atomic.py::atomic_inplace`, which works on a temporary
copy and replaces the original only after a successful save. The caller holds
`library/filelock.py::path_lock` across the complete read-modify-write or move
sequence. Do not put the lock inside `atomic_inplace`: existing callers already
hold the non-reentrant per-path lock. See `test_atomic_writes.py` and
`test_filelock.py`.

**Treat movement as a returned result, not an exception contract.**
`library/mover.py::move` returns `MoveResult(moved, destination, conflict)` and
can report a destination conflict without raising. Every caller must inspect
the result. Move a neighboring `.lrc` through `move_lyric_sidecar`. If disk
movement succeeds but a database update fails, attempt and verify compensation;
if compensation fails, report `DIVERGED` and the true path. See
`test_mover_verify.py`, `test_revert_move_back.py`, and
`test_bug_sweep_rollbacks.py`.

**Resolve destination directories through one choke point.** Use
`library/paths.py::build_destination(..., ensure_dirs=True)` for canonical
library directories. It holds a global resolve-and-create lock so concurrent
files converge on one case/punctuation variant. Failure to scan an existing
parent raises `DestinationUnresolved`; callers must route to review or skip,
never create a possible case twin. See `test_destination_race.py`.

**Keep destructive writes auditable.** A full tag rewrite captures a snapshot
first and records `FileChange`, including branches where tagging succeeded but
movement did not. If a later conflict resolution changes the final location,
repoint the audit row. Pass the pre-move path to `pipeline._upsert_track` so a
move cannot leave a phantom index row. See `test_bug_sweep_apply_paths.py`,
`test_bug_sweep_repo.py`, and `test_bug_sweep_library_integrity.py`.

Cleanup never deletes audio. `library.actions.cleanup_library` defaults to
report-only and quarantines eligible leftovers under the configured quarantine
root when applied. Its own quarantine must remain excluded from walks and
scans. Protected tracks are not moved. See `test_cleanup_library.py` and
`test_cleanup_artist_twins.py`.

## Tags and identification

**One conceptual schema spans every supported format.** `tagging/schema.py`
defines `TrackTags`; full writes dispatch through `tagging/writers/__init__.py`
to FLAC, MP3, WAV, and MP4. A new field is incomplete until every applicable
writer and normalized read path agree. Multi-value metadata stays as native
multiple values, not one separator-joined string. See `test_schema_vorbis.py`,
`test_writers_multivalue.py`, and `test_existing_tags_mbid_readback.py`.

Track and disc totals of zero mean unknown and are omitted. Partial MP4 edits
must preserve the untouched half of `trkn` and `disk` tuples. Clearing a field
must remove its frame or atom rather than write a blank that later reappears.
See `test_partial_clear_and_mp4_totals.py` and
`test_album_link_mp4_totals.py`.

**Normalize every apply path.** Candidate, manual, bulk, and in-library apply
flows call `pipeline.prepare_tags` before a full write so mandatory release
type/status defaults and formatting remain consistent. A rewrite that does not
refetch lyrics or advisory data must carry existing values across the canonical
clear. See `test_bug_sweep_apply_paths.py` and `test_routes_review_actions.py`.

MusicBrainz payloads are untrusted dictionaries: explicit nulls, mixed
artist-credit elements, and missing nested keys are normal. Mirror established
helpers instead of indexing nested structures directly. Album-level fields
such as media and totals must be normalized release-wide, and grouped source
folders must retain album-level election instead of selecting each edition in
isolation. See `test_musicbrainz_credits.py`, `test_infer_release_type.py`, and
`test_album_election.py`.

## Pipeline, tasks, and database state

**All automatic identification reaches the common gates.** New clue or lookup
paths converge on `pipeline._finalize_and_commit`; otherwise dry-run and review
policy can be bypassed. `pipeline._commit_tag_path` checks
`library.duplicates.find_duplicate_tracks` before artwork/lyrics requests and
before any mutation. Duplicate lookup is scoped to the chosen destination
library and excludes the same physical file during in-library retagging. See
`test_pipeline_dry_run_shortcircuit.py` and `test_ingest_duplicates.py`.

`pipeline.enqueue` deduplicates against pending work. Callers explicitly
re-tagging a file already in review use the established `requeue_reviews`
behavior rather than creating a second active job. Use
`models.ACTIVE_JOB_STATUSES` for running-work guards and the model enums for
state/reason values. See `test_pipeline_guard.py`.

**Do not hold SQLite write transactions across network calls.** Commit any
clue/log state before MusicBrainz, AcoustID, artwork, lyrics, or webhook work.
SQLite has one writer; a flushed but uncommitted session can block enqueueing
and UI requests for the entire network timeout.

Long work uses `tasks.run_task` or `run_chain` and reports through `TaskCtx`.
Keep `_live_threads` registration and cancellation events valid until a worker
actually finishes. The stale-job reaper must spare a live thread even when a
long operation has not emitted a recent heartbeat. See `test_tasks_reaper.py`
and `test_tasks_chain.py`.

All persisted datetimes are naive UTC from `timeutil.now_utc`. Convert only at
display boundaries through `main._local_tz`; `scheduler._cron_tz` follows the
same timezone precedence before converting the next fire back to naive UTC.
See `test_timezone_resolution.py` and `test_bug_sweep_core.py`.

## Configuration and web boundaries

`config._Store` writes `settings.json` atomically and serializes read-modify-
write with `transact`. Pipeline-critical settings need save-time validation so
invalid templates or timeouts cannot poison every later job. A user-editable
setting changes four surfaces: `UserSettings`, the settings form,
`main.settings_update`, and the consumer. See `test_config_atomic.py`,
`test_config_validators.py`, and `test_routes_sweep_guards.py`.

Authenticated routes take `Depends(require_auth)`. Mutations use POST;
destructive forms require confirmation. Optional checkboxes must tolerate the
browser omitting them. Long work returns promptly after queueing a tracked job
rather than polling or sleeping in a request handler.

Jinja autoescape covers HTML contexts, not URLs; apply `urlencode` to user
values in query strings. Hand-built `HTMLResponse` content must use
`html.escape`. HTMX endpoints retain a correct plain-form fallback and must
respect response/swap semantics already exercised by route tests. Form-
associated controls may live outside their form, and nested HTMX controls must
not accidentally inherit an ancestor request's parameters. See
`test_routes_review_actions.py`, `test_review_queue_integrity.py`, and
`test_bug_sweep_repo.py`.

Uploads are streamed in bounded chunks and partial files are removed on error.
Remote URLs and payload sizes use the shared network validation/cap helpers;
cover images go through the established decode-bomb and MIME normalization
paths. See `test_routes_upload_retag.py`, `test_net_ssrf.py`, and
`test_decompression_bomb_guard.py`.

All web assets are vendored. Do not introduce CDN requests. Follow
[the frontend guide](../frontend/README.md) for visual and build contracts.
