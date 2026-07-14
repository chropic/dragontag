"""Apply a chosen MusicBrainz match onto a file already in the library.

Extracted from the per-track ``apply-match`` route so the batch "Re-identify
untagged tracks" action and the route share one implementation. Writes the
chosen recording/release onto the file (tags + cover art), preserving the file's
own embedded lyrics/advisory, records an auditable ``FileChange`` (revertable via
``/changes``), and refreshes the ``Track`` row. The file is not moved.

Invariant: all network work (``assemble_tags``, cover fetch) happens *before*
the in-place write under ``path_lock`` — never hold an open DB write transaction
across a network call, and never leave a destructive rewrite unauditable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import env
from ..db import session
from ..identify import musicbrainz as mbq
from ..models import FileChange, LibraryFolder, Track

log = logging.getLogger(__name__)


def apply_match(track_id: int, recording_id: str, release_id: str) -> tuple[bool, str]:
    """Apply ``recording_id``/``release_id`` to the track's file in place.

    Returns ``(ok, message)``. ``ok`` is False (with a human-readable reason) for
    a missing track/file, a failed MusicBrainz lookup, or a failed tag write.
    """
    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            return False, "track not found"
        p = Path(track.path)
        if not p.exists():
            return False, f"{p.name}: file not found on disk."
        folder_id = track.library_folder_id

    from ..identify import existing_tags as _existing
    from ..ingest.pipeline import _tags_to_dict, _upsert_track, prepare_tags
    from ..tagging import snapshot as _snapshot
    from ..tagging.coverart import fetch_for_release
    from ..tagging.partial import read_lyrics
    from ..tagging.writers import write_tags

    try:
        tags = mbq.assemble_tags(release_id=release_id, recording_id=recording_id)
    except Exception as e:
        return False, f"MusicBrainz lookup failed: {e}"
    # Same schema guarantees as the pipeline / review-apply paths (formatting,
    # RELEASETYPE inference, RELEASESTATUS default).
    prepare_tags(None, tags)
    cover = fetch_for_release(tags.mb_album_id) if tags.mb_album_id else None
    if cover:
        tags.cover_bytes = cover.data
        tags.cover_mime = cover.mime
    try:
        from .filelock import path_lock
        with path_lock(p):
            # Full canonical rewrite (every writer clears the existing tag set):
            # snapshot first so the write is auditable/revertable, and carry the
            # file's own embedded lyrics/advisory onto the outgoing tags —
            # assemble_tags brings none of its own.
            original_snapshot = _snapshot.capture(p)
            info = _existing.read(p)
            if tags.advisory is None:
                tags.advisory = info.get("advisory")
            if info.get("has_lyrics"):
                try:
                    tags.lyrics = read_lyrics(p) or None
                except Exception:
                    pass
            write_tags(p, tags)
    except Exception as e:
        return False, f"{p.name}: tag write failed: {e}"

    with session() as s:
        # Audit row so the rewrite shows in /changes and can be reverted.
        # job_id=None: no pipeline job backs this correction.
        s.add(FileChange(
            job_id=None,
            file_path=str(p),
            original_path=str(p),
            original_name=p.name,
            original_tags_json=original_snapshot or {},
            new_tags_json=_tags_to_dict(tags),
            cover_jpg_created=False,
        ))
        folder = s.get(LibraryFolder, folder_id) if folder_id else None
        lib_root = Path(folder.path) if folder else env().library_path
        _upsert_track(s, p, tags, lib_root)
    return True, f"Updated {p.name} from MusicBrainz."
