
## Onboarding & UI
Tasks that shape the first-run experience and the overall visual layer.

- **11** — apply terminal24 theme across all templates (see `L:\Files\Repos\searxng-tui` for reference)
- **10** — first-time setup wizard: configure credentials, library paths, preferences on first boot

11 should come before 10 — the setup wizard is a new UI surface and should be built on the finished theme. Both touch the Jinja2 templates and the settings/config layer.

---

now, new branch, plan polish & release. open pr to main when work is done. before opening pr to main, delete TASKS.md & SESSION_HANDOFF.md, ensure CHANGELOG.md is uptodate.

## Polish & release
Tasks that are largely independent of each other but all need to be done before shipping.

- **12** — hardware/performance: reduce CPU and memory footprint
- **13** — code review and refactor: clean up anything identified across the previous tasks
- **15** — update README and in-code comments; keep comments shorthand and only where necessary
- **16** — release readiness: scrub internal-only information and revealing details from all documentation

These should run roughly in this order: 12 (so the refactor in 13 has the perf changes to work with), 13, 15 (docs written against the stable post-refactor code), 16 (final scrub before public release).

