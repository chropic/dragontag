"""Scan/ingest path filtering.

Applies three user-configurable filter lists:

* ``scan_filter_patterns`` — regex patterns matched against the **filename**
  (not the full path).  Any match causes the file to be skipped.
* ``scan_exclude_dirs`` — absolute directory paths; files whose resolved path
  starts with any of these are skipped.
* ``scan_exclude_files`` — absolute file paths skipped individually.  Edited
  in Settings and also auto-populated when a change is moved back to its
  original directory (so the file isn't immediately re-ingested).

All lists live in ``UserSettings`` and are respected by the scanner, the
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
    exclude_files: list[str] | None = None,
) -> bool:
    """Return True if *p* should be skipped according to the filter lists."""
    name = p.name
    for raw in filter_patterns:
        try:
            if re.search(raw, name):
                return True
        except re.error:
            log.debug("scan_filter_patterns: invalid regex %r (skipped)", raw)
    resolved = p.resolve()
    resolved_str = str(resolved)
    if exclude_files and (str(p) in exclude_files or resolved_str in exclude_files):
        return True
    for d in exclude_dirs:
        resolved_dir = Path(d.rstrip("/\\")).resolve()
        if resolved == resolved_dir or resolved.is_relative_to(resolved_dir):
            return True
    return False
