#!/usr/bin/env python3
"""Bump the patch version across every version-bearing file, in lockstep.

dragontag versions every commit: the ``.githooks/pre-commit`` hook runs this,
which increments the patch segment (``X.Y.Z`` -> ``X.Y.Z+1``) in
``pyproject.toml`` and both package ``__init__.py`` files at once, then the hook
re-stages them so the bump lands in the commit. See ``docs/VERSIONING.md``.

``pyproject.toml`` is the source of truth for the current version; the two
``__init__.py`` files are rewritten to match, so a drift between them heals on
the next bump.

Run standalone to bump by hand::

    python3 scripts/bump_version.py     # prints the new version
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (path, pattern). Each pattern has three groups: the prefix up to the version,
# the X.Y.Z version itself, and the trailing quote — so we can rewrite just the
# number and leave everything else on the line untouched.
_PYPROJECT = re.compile(r'(?m)^(version = ")(\d+\.\d+\.\d+)(")')
_DUNDER = re.compile(r'(?m)^(__version__ = ")(\d+\.\d+\.\d+)(")')

TARGETS: list[tuple[Path, re.Pattern[str]]] = [
    (ROOT / "pyproject.toml", _PYPROJECT),
    (ROOT / "dragontag" / "app" / "__init__.py", _DUNDER),
    (ROOT / "dragontag" / "__init__.py", _DUNDER),
]


def bump_patch(version: str) -> str:
    """Return ``version`` with its patch segment incremented by one."""
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def main() -> int:
    pyproject, pyproject_pat = TARGETS[0]
    match = pyproject_pat.search(pyproject.read_text(encoding="utf-8"))
    if not match:
        print("bump_version: could not find version in pyproject.toml", file=sys.stderr)
        return 1
    new_version = bump_patch(match.group(2))

    for path, pattern in TARGETS:
        text = path.read_text(encoding="utf-8")
        new_text, count = pattern.subn(
            lambda m: f"{m.group(1)}{new_version}{m.group(3)}", text
        )
        if count == 0:
            print(f"bump_version: no version line found in {path}", file=sys.stderr)
            return 1
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")

    print(new_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
