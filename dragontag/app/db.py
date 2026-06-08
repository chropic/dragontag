"""SQLite engine + session helpers.

Why SQLite: this is a single-instance, single-user app. SQLite handles the load
trivially, requires zero ops, and lives in the same ``/config`` volume as the
rest of the persisted state.

``check_same_thread=False`` is required because the background worker thread
(see ``ingest/pipeline.py``) accesses the engine concurrently with the FastAPI
request threads. SQLModel/SQLAlchemy serialize writes internally.
"""
from __future__ import annotations
from sqlalchemy.exc import OperationalError
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from .config import env

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
            connect_args={"check_same_thread": False},
        )
        _migrate(_engine)
        SQLModel.metadata.create_all(_engine)
        _seed_library_folder()
    return _engine


def _migrate(engine):
    with engine.begin() as conn:
        # Each ALTER runs independently so a duplicate-column error on one
        # (already-migrated) column doesn't skip the others.
        for ddl in (
            "ALTER TABLE track ADD COLUMN advisory INTEGER",
            "ALTER TABLE track ADD COLUMN has_lyrics INTEGER DEFAULT 0",
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

    Returns top genres (from each track's ``album`` proxy is too rough — we
    use ``genre`` if present, falling back to album_artist), explicit count,
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
        # Top genres by album_artist as a fallback; without a genre column we
        # synthesize from album_artist counts. If a real genre col is later
        # added, swap here.
        try:
            from sqlalchemy import text as _text
            rows = list(s.exec(_text(
                "SELECT album_artist, COUNT(*) c FROM track "
                "WHERE album_artist IS NOT NULL AND album_artist != '' "
                "GROUP BY album_artist ORDER BY c DESC LIMIT 5"
            )))
            out["top_artists"] = [(r[0], r[1]) for r in rows]
        except Exception:
            pass
    return out


def session() -> Session:
    """Return a new SQLModel session. Use as a context manager:

    .. code-block:: python

        with session() as s:
            s.add(obj); s.commit()
    """
    return Session(engine())
