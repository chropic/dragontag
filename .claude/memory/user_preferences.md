---
name: user-preferences
description: How the maintainer (chropic) likes to collaborate with Claude on this repo
metadata:
  type: user
---

# Working with chropic

## Style

- Terse text output. State results, not running commentary. Final summaries: short, lead with
  the outcome, group details by severity/area.
- Bias toward action when the call is reasonable; ask only when genuinely blocked.
- Always work on a new branch for non-trivial requests; never commit/push without an explicit
  ask (in autonomous/remote sessions where the task itself is "fix and push", pushing + a draft
  PR is the expected deliverable).

## Process expectations

- For multi-phase tasks: present a plan, get a single batch of clarifying questions answered,
  then execute end-to-end without further check-ins unless something changes.
- After execution, give a compact phase-by-phase summary so the maintainer can scan what landed.
- PRs are opened as drafts; bug sweeps use titles like `fix: <area> bug sweep (N fixes)` with a
  severity-grouped body (see merged #39/#40 for the template).
- Verify claims against the code before reporting them — the maintainer expects findings to be
  confirmed, not speculative; deliberately-intentional behavior called out as "not a bug" is
  valued in sweep reports.

## Domain notes

- This is a personal, single-user app. Don't over-engineer for multi-tenant / horizontal-scale
  concerns; SQLite + threads is a feature, not a limitation to fix.
- The maintainer cares most about: never damaging audio files, tag-schema correctness, UI polish
  (the TUI aesthetic is deliberate), and review-queue ergonomics. Throughput is a non-goal.
- "Library" terminology is intentional — see [[conventions]].
- Recurring request pattern: "sweep X for bugs" — expects a verified, ranked findings list,
  fixes with per-bug regression tests, a CHANGELOG section, and a draft PR.
