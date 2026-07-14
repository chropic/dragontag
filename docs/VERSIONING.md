# Versioning

dragontag **versions every commit**. Each commit increments the patch segment of
the version, so every commit on the history has a distinct, monotonically
increasing version number.

## The scheme

- The version is plain semver `MAJOR.MINOR.PATCH` (currently on the `0.1.x` line).
- **Every commit bumps `PATCH` by one** — `0.1.0` → `0.1.1` → `0.1.2` → …
- `MAJOR` / `MINOR` are bumped by hand at meaningful milestones (edit the
  version in `pyproject.toml` and reset `PATCH`); the per-commit automation only
  ever touches `PATCH`.

## Where the version lives

Three files are kept in lockstep, with `pyproject.toml` as the source of truth:

- `pyproject.toml` — `version = "X.Y.Z"`
- `dragontag/app/__init__.py` — `__version__` (surfaced as the FastAPI app version / `/openapi.json`)
- `dragontag/__init__.py` — `__version__`

`scripts/bump_version.py` rewrites all three from the current `pyproject.toml`
value, so if they ever drift, the next bump re-syncs them.

## How it happens automatically

A tracked git hook does the work. Enable it **once per clone**:

```sh
git config core.hooksPath .githooks
```

From then on, `.githooks/pre-commit` runs `scripts/bump_version.py` on every
commit and stages the changed files, so the bump is part of that commit. Nothing
else to remember.

- **Merges** don't bump (the hook skips when `.git/MERGE_HEAD` exists) — the
  merge already carries both sides' versions.
- **Skip a bump for one commit** (rare): `git commit --no-verify`.

## Bumping by hand

You never need to, but if the hook isn't enabled (or you want to bump outside a
commit):

```sh
python3 scripts/bump_version.py   # prints the new version, edits the 3 files
```

## Why every commit

It's a single-user, continuously-deployed app: there are no release trains to
batch changes into, and "which build am I actually running?" is a real question
when a container is rebuilt from `main`. A monotonic per-commit version makes the
running app's `/openapi.json` version answer that precisely, with zero release
ceremony.
