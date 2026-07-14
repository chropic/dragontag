#!/bin/bash
# SessionStart hook — make the clone ready for an agent session.
#
# 1. Enable the tracked git hooks (.githooks) so EVERY commit bumps the patch
#    version in lockstep (see docs/VERSIONING.md). This is cheap, idempotent,
#    and runs in every environment — versioning must never be missed, which is
#    the whole reason this hook exists.
# 2. In Claude Code on the web, create the Python 3.12 venv and install the
#    project (+ dev extras) so pytest / the route tests run without setup, and
#    put .venv/bin on PATH for the session.
#
# Idempotent and non-interactive; safe to run on every session start.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

# --- always: enable the per-commit versioning hook -------------------------
# core.hooksPath is local git config (not committed), so a fresh clone won't
# run .githooks/pre-commit until this is set. Do it in every environment.
if [ -d .githooks ]; then
  git config core.hooksPath .githooks
fi

# --- web only: install dependencies ----------------------------------------
# Skip on local machines so we never clobber a contributor's own setup.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Python >= 3.12 is required; build the venv from python3.12 explicitly.
if [ ! -x .venv/bin/pytest ]; then
  python3.12 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -e ".[dev]"
fi

# Make `pytest` / `python` resolve to the venv for the rest of the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
