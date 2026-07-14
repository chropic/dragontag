"""Configuration: three layers stacked from low-trust to high-trust.

Layer 1 — **Environment variables** (``DRAGONTAG_*``): immutable container config (volume
paths, username, secret file paths). Parsed by ``Env`` using pydantic-settings.

Layer 2 — **Docker secret files**: files referenced by the ``*_FILE`` env vars
(``DRAGONTAG_PASSWORD_FILE``, ``DRAGONTAG_SESSION_SECRET_FILE``, ``DRAGONTAG_ACOUSTID_KEY_FILE``).
Reading the file at request time means rotated secrets are picked up without
rebuilding the image. The file contents win over the inline ``*`` env vars.

Layer 3 — **User settings JSON** (``/config/settings.json``): everything tweakable
from the web UI (separators, thresholds, filename templates). Loaded lazily into
``UserSettings`` and rewritten atomically on every UI update.

Public surface:

* :func:`env`      — returns the immutable ``Env`` (env-var-only) singleton.
* :func:`settings` — returns the current ``UserSettings`` (UI-editable).
* :func:`store`    — returns the underlying ``_Store`` for ``store().update(patch)``.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _check_template(tmpl: str, **placeholders: Any) -> None:
    """Reject a path template with broken syntax or unknown placeholders.

    A bad template (e.g. a ``{name}`` typo) would otherwise save fine and then
    fail *every* ingest at the move step until corrected — a self-inflicted
    pipeline outage with no hint that the setting is the cause.
    """
    try:
        tmpl.format(**placeholders)
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"invalid template {tmpl!r}: {e}") from None


def _read_secret(path: str | None) -> str | None:
    """Read a secret file once. Trailing whitespace is stripped because
    ``echo "value" > secret.txt`` and most editors append a newline."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip() or None


class Separators(BaseModel):
    """Per-tag joiner strings used when rendering multi-value Vorbis fields.

    Defaults match the user's reference convention in ``flac_metadata.md``:
    ``ARTIST`` and ``album_artist`` use ``//`` (a stylistic choice that survives
    in some players that don't split on semicolons), while ``ARTISTS`` and the
    MusicBrainz multi-ID fields use ``;``.

    The ``default`` field is the fallback for any tag not explicitly listed.
    """

    ARTIST: str = "//"
    album_artist: str = "//"
    ARTISTS: str = ";"
    ARTISTSORT: str = ";"
    ALBUMARTISTSORT: str = ";"
    GENRE: str = ";"
    LABEL: str = ";"
    ISRC: str = ";"
    MUSICBRAINZ_ARTISTID: str = ";"
    MUSICBRAINZ_ALBUMARTISTID: str = ";"
    COMPOSER: str = ";"
    CONDUCTOR: str = ";"
    LYRICIST: str = ";"
    ARRANGER: str = ";"
    default: str = ";"


class UserSettings(BaseModel):
    """Settings that can be edited from the web UI.

    Persisted to ``/config/settings.json``. The schema is intentionally flat
    (no nested objects beyond ``Separators``) so the settings page can render
    everything as plain form inputs.
    """

    # ----- identification -----
    acoustid_enabled: bool = True
    # Below this the file is routed to /review. Bounded to the score model's
    # actual [0, 1] range.
    score_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    # Network timeout (seconds) for outbound API calls (MusicBrainz, AcoustID).
    # Without it a half-open connection can hang the single ingest worker
    # thread indefinitely, wedging all further processing. Must be positive —
    # 0 would become socket.setdefaulttimeout(0), failing every MB call.
    network_timeout_seconds: float = Field(default=15.0, gt=0)

    # Hard wall-clock timeout (seconds) for the local fpcalc fingerprinting
    # subprocess. fpcalc occasionally hangs on corrupt/unusual audio; without
    # this bound the single ingest worker thread would block forever and
    # every subsequently queued file would silently stop being processed.
    fingerprint_timeout_seconds: float = Field(default=30.0, gt=0)

    # ----- tag rendering -----
    separators: Separators = Field(default_factory=Separators)
    # genre_limit: max genres written (0 = no limit)
    genre_limit: int = 3
    # genre_casing: "title" (Title Case), "lower" (lowercase), "as-is" (raw MB tags)
    genre_casing: str = "title"
    # genre_whitelist_enabled: filter MB community tags against the vendored
    # canonical genre list (identify/genres.py) so junk like "billboard top
    # 100" never lands in GENRE. Off = raw MB tags as before.
    genre_whitelist_enabled: bool = True
    # skip_fields: list of TrackTags attribute names to omit when writing tags
    skip_fields: list[str] = Field(default_factory=list)

    # ----- library layout -----
    # Placeholders accepted by ``Path.format`` calls in library/paths.py:
    #   {track} {disc} {title} {artist} {ext} {disctotal} {tracktotal}
    filename_template_single: str = "{track:02d}. {title}.{ext}"
    filename_template_multidisc: str = "{track:02d}. {title}.{ext}"
    multidisc_folder_template: str = "Disc {disc}"

    @field_validator("filename_template_single", "filename_template_multidisc")
    @classmethod
    def _validate_filename_template(cls, v: str) -> str:
        # Must render with exactly the keyword set paths.render_filename passes.
        _check_template(
            v, track=1, disc=1, title="t", artist="a", ext="flac",
            disctotal=1, tracktotal=1,
        )
        return v

    @field_validator("multidisc_folder_template")
    @classmethod
    def _validate_disc_folder_template(cls, v: str) -> str:
        # Must render with the keyword set paths.build_destination passes.
        _check_template(v, disc=1, disctotal=1)
        return v

    # Reuse an existing album folder whose name matches after stripping edition
    # suffixes, so "Afraid - Single"/"Afraid (Deluxe)" ingest into an existing
    # "Afraid" instead of minting a suffixed twin (library/paths._reuse_folded_dir).
    fold_edition_suffixes: bool = True
    # Where the Cleanup action quarantines leftover files. Empty =
    # <library_root>/.dragontag-trash. Must be absolute when set.
    quarantine_path: str = ""

    @field_validator("quarantine_path")
    @classmethod
    def _validate_quarantine_path(cls, v: str) -> str:
        # A relative value would resolve against the CWD and scatter trash dirs.
        if v and not Path(v).is_absolute():
            raise ValueError("quarantine_path must be an absolute path")
        return v

    # ----- watcher -----
    watcher_enabled: bool = True
    # fnmatch patterns ignored on top of the always-on extension whitelist
    watcher_ignore_patterns: list[str] = Field(
        default_factory=lambda: ["*.part", "*.tmp", "*.crdownload", ".*"]
    )
    # Number of seconds a file must be untouched before we consider it ready
    # to process. Guards against picking up half-written downloads.
    watcher_settle_seconds: float = 2.0

    # ----- cover art -----
    # If a `cover.jpg` already exists in the album folder, we only overwrite
    # when the *new* image is at least this many pixels wide. Prevents a
    # smaller fingerprint-fallback cover from clobbering a hand-curated one.
    cover_min_overwrite_pixels: int = 1000
    # When a specific release has no cover in the Cover Art Archive, optionally
    # fall back to the release-GROUP cover. That image is shared across every
    # edition in the group, so enabling this can apply the same art to several
    # different releases — left OFF by default to prevent cover bleed.
    cover_allow_release_group_fallback: bool = False

    # ----- replaygain -----
    # Absolute path to the rsgain (or loudgain) binary. Empty = auto-discover on
    # PATH and in common install dirs. Set this when the tool lives somewhere
    # non-standard or isn't on the service's PATH.
    replaygain_tool_path: str = ""

    # ----- library foldering -----
    # Separators on which a multi-artist *album-artist* credit is reduced to its
    # first artist when building the folder name. Empty (default) keeps the full
    # credit intact, so "Tyler, The Creator" and "A & B" stay as single folders.
    # Set e.g. "&,;" to file collaborations under the first artist. Slashes are
    # never honored, so "AC/DC" and dragontag's own "A//B" join stay combined.
    # (Featured-guest suffixes like "feat./ft./featuring …" are always stripped,
    # independent of this setting.)
    folder_artist_split_separators: str = ""

    # ----- lyrics -----
    # Fetch lyrics from LRCLIB and embed them in the audio file.
    # Also runs the explicit-content classifier and writes ITUNESADVISORY.
    lyrics_enabled: bool = True

    # ----- smart formatting -----
    # Apply Title Case to title, album, artist, composer strings.
    format_title_case: bool = False
    # Wrap bare trailing qualifiers (Live, Remix, etc.) in parentheses.
    format_fix_qualifiers: bool = False
    # Grammar correction master toggle. When off, all sub-rules are ignored
    # regardless of their individual state.
    format_grammar_correct: bool = False
    # Sub-rules — each can be enabled independently when the master is on.
    format_grammar_fix_allcaps: bool = True       # lowercase ALL-CAPS + re-title-case
    format_grammar_fix_contractions: bool = True  # DONT → don't, etc.
    format_grammar_fix_possessives: bool = True   # PEOPLES X → people's X
    format_grammar_fix_punct_spacing: bool = True # collapse spaces, fix punctuation

    # Dry-run mode: pipeline identifies and assembles tags but stops before
    # writing to files or moving them. Jobs land in the review queue so the
    # user can inspect and commit individually.
    dry_run: bool = False

    # ----- changes / audit log -----
    # How many FileChange audit rows to keep (pruned oldest-first on insert).
    max_recent_changes: int = Field(default=500, ge=0)

    # ----- scan filters -----
    # Regex patterns matched against filenames (not full paths). Files whose
    # names match any pattern are excluded from all scan/ingest operations.
    # Example: ["\.ini$", "Thumbs\.db$", "\.DS_Store$"]
    scan_filter_patterns: list[str] = Field(default_factory=list)
    # Directories to exclude from all scan/ingest operations, as absolute paths.
    scan_exclude_dirs: list[str] = Field(default_factory=list)
    # Absolute file paths excluded from all scan/ingest operations. Edited in
    # the UI like the other filter lists, and also populated automatically when
    # a change is "moved back" to its original directory so the file isn't
    # immediately re-ingested.
    scan_exclude_files: list[str] = Field(default_factory=list)

    # ----- logging -----
    # 0=silent, 1=errors, 2=warnings, 3=info, 4=debug. Applied at runtime by
    # logsetup.apply() on startup and whenever settings are saved.
    log_verbosity: int = Field(default=3, ge=0, le=4)

    # ----- webhook notifications -----
    webhook_url: str = ""
    webhook_on_done: bool = True
    webhook_on_error: bool = True

    # ----- display -----
    # IANA zone name for displaying timestamps. Empty = UTC. Ignored (and
    # cleared) when the container has a TZ environment variable set — that
    # always wins, since it's the operator's explicit choice.
    timezone: str = ""

    # ----- MusicBrainz client -----
    musicbrainz_user_agent: str = (
        "dragontag/0.1.0 ( https://github.com/chropic/dragontag )"
    )
    musicbrainz_server: str = "musicbrainz.org"

    def merged(self, patch: dict[str, Any]) -> "UserSettings":
        """Return a new ``UserSettings`` with ``patch`` overlaid on top.

        Used by :meth:`_Store.update`. We re-validate via pydantic so bad
        inputs from the form (e.g. negative threshold) are rejected before
        being persisted.
        """
        data = self.model_dump()
        data.update(patch)
        return UserSettings.model_validate(data)


class Env(BaseSettings):
    """Immutable container-level config from ``DRAGONTAG_*`` environment variables.

    ``pydantic-settings`` strips the ``DRAGONTAG_`` prefix and lowercases the rest,
    so e.g. ``DRAGONTAG_LIBRARY_PATH`` -> ``Env.library_path``.
    """

    model_config = SettingsConfigDict(env_prefix="DRAGONTAG_", extra="ignore")

    username: str = "admin"

    # Auth: prefer the *_FILE variant (Docker secret). The plain variant is
    # supported only for local dev (set ``DRAGONTAG_PASSWORD=...``).
    password_file: str | None = None
    password: str | None = None

    session_secret_file: str | None = None
    session_secret: str | None = None

    acoustid_key_file: str | None = None
    acoustid_key: str | None = None

    # Volume mount points. Containers should leave these at the defaults and
    # mount their host directories there; the env vars exist primarily so a
    # local dev run can point at temp paths (see tests/conftest.py).
    library_path: Path = Path("/library")
    drop_path: Path = Path("/drop")
    config_path: Path = Path("/config")

    def resolve_password(self) -> str | None:
        """Return the configured password, checking three sources in priority order:
        Docker secret file → inline env var → wizard-written hash in config dir.
        The config-dir fallback lets the first-run setup wizard set a password
        without requiring a container restart or pre-configured secrets.
        """
        return (
            _read_secret(self.password_file)
            or self.password
            or _read_secret(str(self.config_path / "password.hash"))
        )

    def resolve_session_secret(self) -> str:
        """Return a session signing secret. Falls back to an ephemeral random
        value so a fresh container can still start without configuration —
        existing sessions are simply invalidated on every restart in that case.
        """
        s = _read_secret(self.session_secret_file) or self.session_secret
        if not s:
            s = secrets.token_urlsafe(32)
        return s

    def resolve_acoustid_key(self) -> str | None:
        return (
            _read_secret(self.acoustid_key_file)
            or self.acoustid_key
            or _read_secret(str(self.config_path / "acoustid.key"))
        )


class _Store:
    """Lazy holder for the env+settings pair, with JSON persistence.

    Kept as a class (not module globals) so tests can construct an isolated
    instance against a temp dir if needed. In production a single module-level
    singleton is constructed via :func:`store`.
    """

    def __init__(self) -> None:
        self.env = Env()
        # Ensure the config dir exists before we try to read/write settings.json.
        # On a bare ``docker compose up`` with a fresh volume, this is the
        # first thing that touches the mount.
        self.env.config_path.mkdir(parents=True, exist_ok=True)
        self._settings_path = self.env.config_path / "settings.json"
        # Guards the read-merge-write in update(): two concurrent callers
        # (e.g. a settings POST racing move_back's scan_exclude_files append)
        # would otherwise both read the same self.user, merge their own
        # patch, and write — the second write silently discards the first.
        self._update_lock = threading.Lock()
        self.user = self._load()

    def _load(self) -> UserSettings:
        """Read settings.json if it exists; on corruption, fall back to defaults.

        We don't raise on a bad JSON file because a broken settings file
        shouldn't prevent the app from booting (the user couldn't fix it
        without the UI running).
        """
        if self._settings_path.exists():
            try:
                data = json.loads(self._settings_path.read_text("utf-8"))
                # Migration: scan_exempt_paths (≤0.8) was merged into the scan
                # filters as scan_exclude_files.
                legacy = data.pop("scan_exempt_paths", None)
                if legacy:
                    files = data.get("scan_exclude_files") or []
                    data["scan_exclude_files"] = files + [
                        p for p in legacy if p not in files
                    ]
                u = UserSettings.model_validate(data)
                if legacy:
                    self._save(u)
                return u
            except Exception:
                pass
        u = UserSettings()
        self._save(u)
        return u

    def _save(self, u: UserSettings) -> None:
        # ensure_ascii=False keeps unicode (artist names, etc.) readable in
        # the on-disk file. Write to a sibling temp file and atomically
        # ``os.replace`` it into place so a crash mid-write can never leave a
        # truncated settings.json behind (which ``_load`` would silently treat
        # as corrupt and replace with defaults, wiping the user's config).
        data = json.dumps(u.model_dump(), indent=2, ensure_ascii=False)
        tmp = self._settings_path.with_name(self._settings_path.name + ".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self._settings_path)
        except OSError as e:
            import logging
            logging.getLogger(__name__).warning("settings: could not write %s: %s", self._settings_path, e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def update(self, patch: dict[str, Any]) -> UserSettings:
        """Merge ``patch`` over the current settings, persist, and return."""
        with self._update_lock:
            self.user = self.user.merged(patch)
            self._save(self.user)
            return self.user

    def transact(self, fn) -> UserSettings:
        """Atomic read-modify-write: ``fn(current_settings) -> patch dict``.

        ``update()`` alone only protects the merge-and-save step; a caller
        that reads ``settings()`` *before* calling ``update`` (e.g. to append
        a path to a list) can still race another such caller and lose one of
        the two appends. ``transact`` runs ``fn`` under the same lock that
        guards the write, so the read it depends on is current.
        """
        with self._update_lock:
            patch = fn(self.user)
            self.user = self.user.merged(patch)
            self._save(self.user)
            return self.user


_store: _Store | None = None
_store_lock = threading.Lock()


def store() -> _Store:
    """Return the process-wide settings store (constructed lazily).

    Double-checked locking: without the lock, two threads racing the very
    first call could both see ``_store is None``, each construct their own
    ``_Store`` (re-reading/re-writing settings.json), with the loser's
    instance silently discarded.
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _Store()
    return _store


def reset_store() -> None:
    """Drop the singleton so the next ``store()`` call re-reads settings.json.

    Used by backup restore after swapping the config files on disk.
    """
    global _store
    _store = None


def env() -> Env:
    """Shortcut for ``store().env``."""
    return store().env


def settings() -> UserSettings:
    """Shortcut for ``store().user``. Call from request handlers / pipeline."""
    return store().user
