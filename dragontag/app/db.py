"""SQLite engine + session helpers.

Why SQLite: this is a single-instance, single-user app. SQLite handles the load
trivially, requires zero ops, and lives in the same ``/config`` volume as the
rest of the persisted state.

``check_same_thread=False`` is required because the background worker thread
(see ``ingest/pipeline.py``) accesses the engine concurrently with the FastAPI
request threads. SQLModel/SQLAlchemy serialize writes internally.

To survive that concurrency the engine runs SQLite in WAL mode with a busy
timeout: WAL lets readers and a single writer proceed at once, and the busy
timeout makes a contended writer wait-and-retry rather than fail immediately
with "database is locked".
"""
from __future__ import annotations
import logging
from sqlalchemy.exc import OperationalError
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from .config import env

log = logging.getLogger(__name__)

_engine = None


def engine():
    """Lazy engine constructor.

    Deferred so test setup can override ``DRAGONTAG_CONFIG_PATH`` *before* the
    SQLite file is created. ``SQLModel.metadata.create_all`` is idempotent
    so it's safe to call on every boot.
    """
    global _engine
    if _engine is None:
        db_path = env().config_path / "dragontag.db"
        _engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            # Run once per new DBAPI connection. WAL + busy_timeout are what keep
            # the worker thread and request threads from colliding with
            # "database is locked"; synchronous=NORMAL is the WAL-safe default.
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        _migrate(_engine)
        SQLModel.metadata.create_all(_engine)
        _seed_library_folder()
    return _engine


def reset_engine() -> None:
    """Dispose the engine so the next ``engine()`` call reopens the DB file.

    Used by backup restore after swapping ``dragontag.db`` on disk.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def _migrate(engine):
    with engine.begin() as conn:
        # Each ALTER runs independently so a duplicate-column error on one
        # (already-migrated) column doesn't skip the others.
        for ddl in (
            "ALTER TABLE track ADD COLUMN advisory INTEGER",
            "ALTER TABLE track ADD COLUMN has_lyrics INTEGER DEFAULT 0",
            "ALTER TABLE job ADD COLUMN kind VARCHAR DEFAULT 'ingest'",
            "ALTER TABLE job ADD COLUMN progress_current INTEGER",
            "ALTER TABLE job ADD COLUMN progress_total INTEGER",
            "ALTER TABLE job ADD COLUMN dry_run_override INTEGER",
            "ALTER TABLE job ADD COLUMN progress_item VARCHAR",
            "ALTER TABLE track ADD COLUMN protected INTEGER DEFAULT 0",
            "ALTER TABLE track ADD COLUMN mb_release_group_id VARCHAR",
        ):
            try:
                conn.execute(text(ddl))
            except OperationalError:
                # Catches both "no such table" and "duplicate column name".
                pass


def _seed_library_folder() -> None:
    """Insert a default LibraryFolder from env().library_path if the table is empty.

    Keeps single-library deployments working transparently after the upgrade.
    Called once per engine construction (i.e. once per process).
    """
    from .models import LibraryFolder
    with Session(_engine) as s:
        if s.exec(select(LibraryFolder)).first():
            return
        s.add(LibraryFolder(path=str(env().library_path), label="Default library"))
        s.commit()


def dashboard_stats() -> dict:
    """Aggregate library stats for the dashboard.

    Returns the top artists (by ``album_artist`` count), explicit count,
    lyrics count, and average duration (formatted mm:ss).
    """
    from sqlalchemy import func as _func, case
    from .models import Track
    out: dict = {
        "top_artists": [],
        "explicit_count": 0,
        "lyrics_count": 0,
        "avg_duration": "—",
    }
    with Session(engine()) as s:
        # advisory == 1 → explicit
        out["explicit_count"] = s.exec(
            select(_func.count(Track.id)).where(Track.advisory == 1)
        ).one() or 0
        # lyrics_count: tracks with embedded lyrics, populated from the file's
        # own lyrics tags during scan/tag (see Track.has_lyrics).
        out["lyrics_count"] = s.exec(
            select(_func.count(Track.id)).where(Track.has_lyrics == True)  # noqa: E712
        ).one() or 0
        avg = s.exec(select(_func.avg(Track.duration))).one()
        if avg:
            m = int(avg) // 60
            sec = int(avg) % 60
            out["avg_duration"] = f"{m}:{sec:02d}"
        # Top artists by album_artist count. (No genre column exists yet; if one
        # is later added this is the place to surface top genres too.)
        try:
            from sqlalchemy import text as _text
            rows = list(s.exec(_text(
                "SELECT album_artist, COUNT(*) c FROM track "
                "WHERE album_artist IS NOT NULL AND album_artist != '' "
                "GROUP BY album_artist ORDER BY c DESC LIMIT 5"
            )))
            out["top_artists"] = [(r[0], r[1]) for r in rows]
        except Exception:
            log.exception("dashboard_stats: top_artists query failed")
    return out


def session() -> Session:
    """Return a new SQLModel session. Use as a context manager:

    .. code-block:: python

        with session() as s:
            s.add(obj); s.commit()
    """
    return Session(engine())
