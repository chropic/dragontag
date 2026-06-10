"""CLI restore for when the web UI can't boot.

Usage (inside the container, with the config volume mounted):

    python -m dragontag.tools.restore_backup /config/backups/dragontag-backup-XXXX.tar.gz

Respects ``DRAGONTAG_CONFIG_PATH`` like the app itself.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    from dragontag.app.backup import restore_bundle
    try:
        print(restore_bundle(Path(argv[0])))
        return 0
    except ValueError as e:
        print(f"Restore refused: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
