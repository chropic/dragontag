# dragontag — Claude memory index

Quick-reference index. Each entry is a one-line hook into the file alongside it. Open the linked
file for details before acting on a memory. Root [CLAUDE.md](../../CLAUDE.md) is the 2-minute
orientation; these files are the depth.

- [Project overview](project_overview.md) — what dragontag is, its surfaces, and where truth lives
- [Architecture map](architecture.md) — module layout, job state machine, threading, locking, invariants
- [Coding conventions](conventions.md) — style, terminology, tag-schema and template/route rules
- [Workflow](workflow.md) — dev environment, branching, tests, CHANGELOG, per-commit versioning, PR discipline
- [Gotchas](gotchas.md) — bug patterns actually found and fixed here; read before writing file-moving, tag-writing, threading, or template code
- [Testing](testing.md) — test layout, conftest tricks, how to test each subsystem, exact signatures tests keep getting wrong
- [User preferences](user_preferences.md) — how the maintainer (chropic) likes to collaborate

Maintenance rule: when you fix a bug class, add it to `gotchas.md`; when you add a subsystem,
extend `architecture.md`; when a workflow step changes (CI, build, test invocation), fix
`workflow.md` in the same PR. Stale memory is worse than no memory.
