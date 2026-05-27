## Library & file management
Tasks that all touch the concept of a tracked music library, where files live, and how jobs route into it.

- **2** — build a database of the user's music library (multi-folder, settable in settings); files dropped through the interface ultimately land there
- **7** — organize/rename existing library folders based on tags (artist/album/disc hierarchy)
- **8** — bulk operations: re-tag everything from a given source folder

These share the library-path model, the mover/paths layer, and the DB schema for tracking known files. 7 and 8 both assume 2 exists.

---

## Lyrics pipeline
Tasks that produce and consume per-track lyric data.

- **4** — fetch lyrics from lrcget or other services; store them in the DB or embed in files
- **3** — explicit/non-explicit auto-tagger using lyric files (see `L:\Files\Repos\autoadvisory` for the classifier logic)

4 must come before 3 — the auto-tagger consumes lyrics that 4 fetches. Both touch the ingest pipeline and the TrackTags schema (a `lyrics` field will be needed for embedding LRC/plain lyrics in files).

---

## Per-job UX & review flow
Tasks that extend what the user can do with an individual job before or during tagging.

- **5** — dry-run mode: preview destination path and final tags without writing anything
- **6** — per-job cover art picker: choose from multiple candidates or upload a custom image

Both live in the review UI and the `_commit_tag_path` path. 5 requires surfacing the assembled TrackTags and computed destination in the UI without side-effects; 6 extends the same review form with cover art selection.

---

## Docker, security & CI/CD
Tasks that touch the container config, permissions, and the release pipeline.

- **9** — security hardening: run under bare-minimum Docker capabilities/permissions; document which caps are needed and why
- **14** — GitHub Actions: automate Docker image builds and pushes; update docker-compose and docs to reference the published image

9 and 14 both modify `Dockerfile`, `docker-compose.yml`, and surrounding documentation. The security work in 9 defines what the final image looks like; 14 automates producing it.

---

## Onboarding & UI
Tasks that shape the first-run experience and the overall visual layer.

- **11** — apply terminal24 theme across all templates (see `L:\Files\Repos\searxng-tui` for reference)
- **10** — first-time setup wizard: configure credentials, library paths, preferences on first boot

11 should come before 10 — the setup wizard is a new UI surface and should be built on the finished theme. Both touch the Jinja2 templates and the settings/config layer.

---

## Polish & release
Tasks that are largely independent of each other but all need to be done before shipping.

- **12** — hardware/performance: reduce CPU and memory footprint
- **13** — code review and refactor: clean up anything identified across the previous tasks
- **15** — update README and in-code comments; keep comments shorthand and only where necessary
- **16** — release readiness: scrub internal-only information and revealing details from all documentation

These should run roughly in this order: 12 (so the refactor in 13 has the perf changes to work with), 13, 15 (docs written against the stable post-refactor code), 16 (final scrub before public release).
