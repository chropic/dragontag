"""SQLite engine + session helpers.

Why SQLite: this is a single-instance, single-user app. SQLite handles the load
trivially, requires zero ops, and lives in the same ``/config`` volume as the
rest of the persisted state.

``check_same_thread=False`` is required because the background worker thread
(see ``ingest/pipeline.py``) accesses the engine concurrently with the FastAPI
request threads. SQLModel/SQLAlchemy serialize writes internally.
"""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

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
        db_path = env().config_path / "aio-tagger.db"
        _engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(_engine)
    return _engine


def session() -> Session:
    """Return a new SQLModel session. Use as a context manager:

    .. code-block:: python

        with session() as s:
            s.add(obj); s.commit()
    """
    return Session(engine())
