"""SQLModel tables.

We keep schema small: a single ``Job`` row per ingested file is enough. Its
``status`` field follows a strict state machine (see :class:`JobStatus`), and
when a job stops in ``needs_review`` the structured data needed to resume it
(candidates list, the chosen tags so far) is stored as JSON.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, String
from sqlmodel import Column, Field, SQLModel

from .timeutil import now_utc

# Hard cap on a job's ``log`` column. The log grows by string concatenation as a
# job progresses; without a ceiling a long-running or pathological job (e.g. a
# bulk re-tag emitting a line per track over a huge library) can bloat the DB row
# and the memory needed to load/rewrite it. We keep the most recent bytes.
MAX_JOB_LOG_BYTES = 256 * 1024


def append_job_log(existing: str | None, addition: str) -> str:
    """Append ``addition`` to a job log, retaining only the last MAX_JOB_LOG_BYTES."""
    text = (existing or "") + addition
    marker = "…[earlier log truncated]…\n"
    # Measure and truncate in encoded bytes, not characters — non-ASCII track
    # names would otherwise let the row grow up to ~4x the intended cap.
    raw = text.encode("utf-8")
    if len(raw) > MAX_JOB_LOG_BYTES:
        keep = MAX_JOB_LOG_BYTES - len(marker.encode("utf-8"))
        # errors="ignore" drops a partial multi-byte sequence at the cut point.
        text = marker + raw[-keep:].decode("utf-8", errors="ignore")
    return text


class LibraryFolder(SQLModel, table=True):
    """A configured root directory that receives tagged files."""

    id: int | None = Field(default=None, primary_key=True)
    path: str                          # absolute path on disk
    label: str = ""                    # user-friendly display name
    enabled: bool = True
    priority: int = 0                  # lower value = preferred when routing
    created_at: datetime = Field(default_factory=now_utc)


class Track(SQLModel, table=True):
    """One row per audio file known to be in a library folder."""

    id: int | None = Field(default=None, primary_key=True)
    library_folder_id: int | None = Field(default=None, foreign_key="libraryfolder.id", index=True)

    # Unique absolute path — the canonical identifier for this file.
    path: str = Field(sa_column=Column(String, unique=True, nullable=False))

    # Denormalized tag snapshot for fast display / path computation.
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    track_num: int | None = None
    track_total: int | None = None
    disc_num: int | None = None
    disc_total: int | None = None
    duration: float | None = None
    mb_track_id: str | None = None
    mb_album_id: str | None = None
    mb_release_group_id: str | None = None
    # First MusicBrainz album-artist id. Keys artist-folder unification across
    # alias/credit variants (FERG/A$AP Ferg) that fold to different strings.
    mb_album_artist_id: str | None = None
    advisory: int | None = Field(default=None)
    has_lyrics: bool = Field(default=False)

    # When set, library-wide batch actions (fetch lyrics/covers, advisories,
    # ReplayGain, the nuclear option, bulk re-tag) skip this track entirely.
    # Toggled from the per-track edit menu after a manual correction.
    protected: bool = Field(default=False)

    last_seen: datetime = Field(default_factory=now_utc)
    indexed_at: datetime = Field(default_factory=now_utc)


class JobStatus(str, Enum):
    """Job state machine.

    Transitions (happy path):
        queued -> identifying -> tagging -> moving -> done

    Branches:
        any of the above -> error           (uncaught exception)
        any of the above -> needs_review    (low score / no match / conflict)
        needs_review     -> tagging|moving  (after a user resolves it)
        needs_review     -> skipped         (user dismissed it)
    """

    queued = "queued"
    identifying = "identifying"
    tagging = "tagging"
    moving = "moving"
    running = "running"  # generic background task (scan, organize, …)
    done = "done"
    needs_review = "needs_review"
    error = "error"
    skipped = "skipped"


# Statuses that mean "work is still happening (or about to)". Shared by the
# jobs UI, the progress endpoint, restart recovery and the restore guard so
# the definition can't silently diverge.
ACTIVE_JOB_STATUSES = (
    JobStatus.queued,
    JobStatus.identifying,
    JobStatus.tagging,
    JobStatus.moving,
    JobStatus.running,
)


class ReviewReason(str, Enum):
    """Why a job landed in the review queue (drives UI rendering)."""

    low_score = "low_score"  # best candidate was below the configured threshold
    no_match = "no_match"  # no MB or AcoustID candidate at all
    destination_conflict = "destination_conflict"  # target path already exists
    cover_fetch_failed = "cover_fetch_failed"  # CAA unreachable (5xx/SSL); retriable
    missing_releasetype = "missing_releasetype"  # MB release-group has no primary-type
    dry_run = "dry_run"  # dry-run mode: preview without writing
    destination_unresolved = "destination_unresolved"  # library dir scan failed; moving could mint a case twin
    album_mismatch = "album_mismatch"  # file doesn't appear on the release its album folder matched


class Job(SQLModel, table=True):
    """One row per file ingested through the pipeline."""

    id: int | None = Field(default=None, primary_key=True)

    # ``source_path`` may change after a successful move (the file is no
    # longer there). ``original_name`` is preserved for display in the UI.
    source_path: str
    original_name: str

    # "ingest" for pipeline jobs; other kinds ("scan", "organize", …) are
    # background tasks surfaced in the jobs list via tasks.run_task.
    kind: str = Field(default="ingest")

    # Coarse progress for long-running tasks (None = indeterminate).
    progress_current: int | None = None
    progress_total: int | None = None
    # Short label of the item currently being processed ("Disc 1/03. Song.flac")
    # so the progress bar can show *what* is happening, not just how much.
    progress_item: str | None = None

    # Per-job dry-run override from the Library page checkboxes. None means
    # "follow the global settings().dry_run"; True/False is an explicit choice
    # for this job only and never mutates the global setting.
    dry_run_override: bool | None = None

    status: JobStatus = Field(default=JobStatus.queued, index=True)
    created_at: datetime = Field(default_factory=now_utc, index=True)
    updated_at: datetime = Field(default_factory=now_utc, index=True)

    review_reason: ReviewReason | None = None
    score: float | None = None  # final confidence score of the chosen candidate

    error: str | None = None  # only set when status == error
    log: str = Field(default="")  # human-readable progress messages

    # Top-N candidates from the identifier (recording_id, release_id, score,
    # title, album). Used to render the review UI without re-querying MB.
    candidates_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # The TrackTags dict we *intended* to write — saved before the move so the
    # review UI can show a diff and so a conflict-resolution apply doesn't
    # need to re-fetch from MB.
    chosen_tags_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Final landing path, or the would-be path when blocked on a conflict.
    destination_path: str | None = None

    # FK to the Track row created/updated when this job completed.
    track_id: int | None = Field(default=None, foreign_key="track.id")


class IncompleteAlbum(SQLModel, table=True):
    """An album whose local track count is below the MusicBrainz track count.

    Written (delete-then-insert per folder) by ``library.actions
    .find_missing_tracks`` and rendered on the Library page's "Incomplete" tab.
    Rows are advisory only — dismissing one never touches files.
    """

    id: int | None = Field(default=None, primary_key=True)
    library_folder_id: int | None = Field(default=None, foreign_key="libraryfolder.id", index=True)
    mb_album_id: str = Field(index=True)
    album: str = ""
    artist: str = ""
    local_count: int = 0
    expected_count: int = 0
    missing_titles_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    checked_at: datetime = Field(default_factory=now_utc)


class ScheduledTask(SQLModel, table=True):
    """A cron-scheduled recurring task (see ``scheduler.py``).

    ``task_type`` is one of the keys in ``scheduler.TASK_TYPES``; ``params_json``
    carries the task's arguments (``folder_id``, ``source_path``, ``dry_run``).
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str
    cron: str                          # standard 5-field cron expression
    task_type: str
    params_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = True
    created_at: datetime = Field(default_factory=now_utc)
    last_run_at: datetime | None = None
    last_status: str | None = None     # "ok" | "error: …" | "skipped: …"
    next_run_at: datetime | None = None


class FileChange(SQLModel, table=True):
    """Audit row for one destructive tag-write + move, enabling an undo.

    Written by the pipeline just after a file is successfully tagged and moved
    (see ``ingest/pipeline._commit_tag_path``). ``original_tags_json`` is a full
    pre-write snapshot of the file's tags (``tagging/snapshot.capture``); a
    revert rewrites those tags in place at ``file_path`` and removes the
    ``cover.jpg`` sidecar if we created it. The file is *not* moved back.
    """

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True)
    created_at: datetime = Field(default_factory=now_utc, index=True)

    # Where the file lives now (the post-move library path) and where it came
    # from before the move — kept for display / diagnostics.
    file_path: str
    original_path: str | None = None
    original_name: str = ""

    # Full pre-write tag snapshot: {"format": ext, "tags": {key: [values]}}.
    original_tags_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # The tags we wrote (for showing a before/after in the UI).
    new_tags_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # True only when we created a new cover.jpg (so revert may safely remove it).
    cover_jpg_created: bool = False

    reverted_at: datetime | None = None
    revert_error: str | None = None
