"""Runtime log-level control.

The UI exposes a 0–4 verbosity slider (``UserSettings.log_verbosity``); this
module maps it onto the stdlib logging hierarchy. Setting the root logger is
enough for app modules (they all use ``logging.getLogger(__name__)``), while a
few chatty third-party libraries are pinned at WARNING so debug mode stays
about dragontag, not urllib3 socket chatter.
"""
from __future__ import annotations

import logging

# 0 uses a level above CRITICAL so nothing — not even CRITICAL — is emitted.
_LEVELS = {
    0: logging.CRITICAL + 10,  # silent
    1: logging.ERROR,
    2: logging.WARNING,
    3: logging.INFO,
    4: logging.DEBUG,
}

_NOISY_LIBS = ("musicbrainzngs", "urllib3", "watchdog", "PIL")


def apply(verbosity: int) -> None:
    """Apply a 0–4 verbosity level to all loggers. Safe to call repeatedly."""
    level = _LEVELS.get(int(verbosity), logging.INFO)
    logging.getLogger().setLevel(level)
    for name in _NOISY_LIBS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
