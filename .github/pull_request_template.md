<!--
  Thanks for contributing to dragontag! 🐉
  This template is a scaffold, not a gate. Fill in what's relevant, delete what
  isn't, and keep it readable. A short, honest PR beats a long, padded one.

  A few house rules worth remembering (see CLAUDE.md for the full list):
    • Work on a topic branch; update the CHANGELOG.md "WIP" section.
    • Re-run `bash frontend/build_css.sh` if you touched templates or CSS.
    • New tag fields go into all four writers + schema; new settings touch
      four places; every file move/tag write holds a path_lock.
-->

## Summary

<!-- One or two plain sentences: what does this PR do, and why does it matter?
     Write for a reviewer who has never seen the issue. -->



## What changed and why

<!-- The story of the change. Group related edits; explain the reasoning, not
     just the diff. Bullet points are fine. Link the issue if there is one. -->

-

## Type of change

<!-- Tick every box that applies (put an "x" between the brackets: [x]).
     The words carry the meaning — you don't need the checkbox to read it. -->

- [ ] Bug fix — corrects incorrect behaviour without changing the public surface
- [ ] New feature — adds behaviour users can see or invoke
- [ ] Refactor / internal — no behaviour change, code health only
- [ ] Documentation — docs, comments, or agent memory only
- [ ] Chore / tooling — build, CI, dependencies, project config
- [ ] Breaking change — existing behaviour, data, or config changes meaning

## How this was tested

<!-- How would a reviewer convince themselves this works? Commands, manual
     steps, before/after. "It passes CI" alone is not evidence — say what CI
     actually exercises for this change. -->

- [ ] `pytest -q` passes locally
- [ ] Exercised the affected flow by hand (describe below)
- [ ] Not applicable (explain why)

Details:



## Screenshots or recordings

<!-- Only for user-visible changes. Please add descriptive alt text in the
     square brackets so the image is meaningful to screen-reader users and when
     it fails to load: ![what the image shows](url) — not ![screenshot](url). -->

_None — no user-visible change._

## Reviewer notes

<!-- Anything that helps review: risky spots, decisions you're unsure about,
     follow-ups you deliberately deferred, areas that need a closer look. -->



## Checklist

<!-- Be honest — an unchecked box is useful information, not a failure. -->

- [ ] Commits are scoped and their messages explain the "why"
- [ ] `CHANGELOG.md` "WIP" section updated (grouped Added / Changed / Fixed)
- [ ] Version bumped for this change (see `docs/VERSIONING.md`)
- [ ] Tests added or updated for the change
- [ ] Docs / in-app help / agent memory updated if behaviour changed
- [ ] `bash frontend/build_css.sh` re-run if templates or CSS changed
- [ ] No secrets, credentials, or internal hostnames in the diff
