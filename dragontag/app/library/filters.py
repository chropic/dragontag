"""Scan/ingest path filtering.

Applies two user-configurable filter lists:

* ``scan_filter_patterns`` — regex patterns matched against the **filename**
  (not the full path).  Any match causes the file to be skipped.
* ``scan_exclude_dirs`` — absolute directory paths; files whose resolved path
  starts with any of these are skipped.

Both lists live in ``UserSettings`` and are respected by the scanner, the
bulk-retag enqueue, and the drop-folder watcher.
"""
from __future__ import annotations

import re
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def is_path_excluded(
    p: Path,
    filter_patterns: list[str],
    exclude_dirs: list[str],
) -> bool:
    """Return True if *p* should be skipped according to the filter lists."""
    name = p.name
    for raw in filter_patterns:
        try:
            if re.search(raw, name):
                return True
        except re.error:
            log.debug("scan_filter_patterns: invalid regex %r (skipped)", raw)
    resolved = str(p.resolve())
    for d in exclude_dirs:
        d = d.rstrip("/\\")
        if resolved == d or resolved.startswith(d + "/") or resolved.startswith(d + "\\"):
            return True
    return False
