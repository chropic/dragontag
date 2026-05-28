---
name: user-preferences
description: How the maintainer (chropic) likes to collaborate with Claude on this repo
metadata:
  type: user
---

# Working with chropic

## Style

- Terse text output. State results, not running commentary. Final summaries: 1–2 sentences.
- Bias toward action when the call is reasonable; ask only when genuinely blocked.
- Always work on a new branch for non-trivial requests; never commit/push without an explicit ask.

## Process expectations

- For multi-phase tasks: present a plan, get a single batch of clarifying questions answered, then execute end-to-end without further check-ins unless something changes.
- After execution, give a compact phase-by-phase summary so the maintainer can scan what landed.
- PRs are opened on request, not automatically.

## Domain notes

- This is a personal, single-user app. Don't over-engineer for multi-tenant / horizontal-scale concerns.
- The maintainer cares more about UI polish, tag-schema correctness, and review-queue ergonomics than about throughput.
- "Library" terminology is intentional — see [[conventions]].
