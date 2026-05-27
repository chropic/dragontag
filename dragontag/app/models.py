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


class LibraryFolder(SQLModel, table=True):
    """A configured root directory that receives tagged files."""

    id: int | None = Field(default=None, primary_key=True)
    path: str                          # absolute path on disk
    label: str = ""                    # user-friendly display name
    enabled: bool = True
    priority: int = 0                  # lower value = preferred when routing
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
    advisory: int | None = Field(default=None)

    last_seen: datetime = Field(default_factory=datetime.utcnow)
    indexed_at: datetime = Field(default_factory=datetime.utcnow)


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
    done = "done"
    needs_review = "needs_review"
    error = "error"
    skipped = "skipped"


class ReviewReason(str, Enum):
    """Why a job landed in the review queue (drives UI rendering)."""

    low_score = "low_score"  # best candidate was below the configured threshold
    no_match = "no_match"  # no MB or AcoustID candidate at all
    destination_conflict = "destination_conflict"  # target path already exists
    missing_releasetype = "missing_releasetype"  # MB release-group has no primary-type
    dry_run = "dry_run"  # dry-run mode: preview without writing


class Job(SQLModel, table=True):
    """One row per file ingested through the pipeline."""

    id: int | None = Field(default=None, primary_key=True)

    # ``source_path`` may change after a successful move (the file is no
    # longer there). ``original_name`` is preserved for display in the UI.
    source_path: str
    original_name: str

    status: JobStatus = Field(default=JobStatus.queued, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)

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
