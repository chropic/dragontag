"""SQLite engine + session helpers.

Why SQLite: this is a single-instance, single-user app. SQLite handles the load
trivially, requires zero ops, and lives in the same ``/config`` volume as the
rest of the persisted state.

``check_same_thread=False`` is required because the background worker thread
(see ``ingest/pipeline.py``) accesses the engine concurrently with the FastAPI
request threads. SQLModel/SQLAlchemy serialize writes internally.
"""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, select

from .config import env

_engine = None


def engine():
    """Lazy engine constructor.

    Deferred so test setup can override ``AIO_CONFIG_PATH`` *before* the
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
        SQLModel.metadata.create_all(_engine)
        _seed_library_folder()
    return _engine


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


def session() -> Session:
    """Return a new SQLModel session. Use as a context manager:

    .. code-block:: python

        with session() as s:
            s.add(obj); s.commit()
    """
    return Session(engine())
