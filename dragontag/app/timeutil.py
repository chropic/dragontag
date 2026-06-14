"""Small time helpers.

``datetime.utcnow()`` is deprecated on Python 3.12+. dragontag stores and
compares *naive* UTC datetimes everywhere — the SQLite columns and the
croniter-based scheduler both work with naive values, and mixing in
timezone-aware datetimes would raise ``TypeError`` on comparison. So this
returns an aware UTC ``now`` stripped back to naive: identical semantics to the
old ``utcnow()``, without the deprecation warning.
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Naive UTC ``datetime.now`` — drop-in for the deprecated ``utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
