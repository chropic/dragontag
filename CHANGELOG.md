<!-- AGENTS: When a task from TODO.md is completed, append a new entry to the bottom of this file following the same format. Do not edit existing entries. -->

## Task 0 — Rename project to dragontag, port to 7593
**Branch:** `task/0-rename-dragontag` → PR #1

- Renamed Python package directory `aio_tagger/` → `dragontag/`
- Replaced all `aio-tagger` / `aio_tagger` references with `dragontag` across source, config, Dockerfile, docker-compose, README, LICENSE, and tests
- Changed port from `8080` to `7593` in Dockerfile, docker-compose.yml, and README

---

## Task 1 — More tag fields and customization options
**Branch:** `task/1-more-tags` → PR #2

### New tag fields
Six new fields added to `TrackTags` and written to all supported formats (FLAC/MP3/WAV/MP4):

| Field | Source | FLAC key | ID3 frame | MP4 atom |
|---|---|---|---|---|
| `conductor` | MB recording artist-relations | `CONDUCTOR` | `TPE3` | freeform |
| `lyricist` | MB work artist-relations | `LYRICIST` | `TEXT` | freeform |
| `arranger` | MB work artist-relations | `ARRANGER` | `TXXX:ARRANGER` | freeform |
| `catalog_number` | MB label-info | `CATALOGNUMBER` | `TXXX:CATALOGNUMBER` | freeform |
| `language` | MB text-representation | `LANGUAGE` | `TLAN` | freeform |
| `compilation` | Derived from release-group type | `COMPILATION` | `TCMP` | `cpil` |

`acoustid_id` was already in the schema but never populated. Fixed by switching from the high-level `acoustid.match()` to `fingerprint_file()` + `lookup()`, which exposes the AcoustID UUID in the API response.

### New customization options (all in settings UI + persisted to settings.json)
- **`genre_limit`** — max genres written per track (default `3`, set `0` for no limit)
- **`genre_casing`** — `"title"` (default) / `"lower"` / `"as-is"` (raw MB tag strings)
- **`skip_fields`** — checklist of `TrackTags` attribute names; checked fields are suppressed at write time across all formats
- **All multi-value separators** now exposed in settings UI — `ARTISTSORT`, `ALBUMARTISTSORT`, `LABEL`, `ISRC`, `COMPOSER`, `CONDUCTOR`, `LYRICIST`, `ARRANGER` were previously hardcoded to `";"`

### Files changed
`dragontag/app/tagging/schema.py`, `dragontag/app/tagging/writers/_id3common.py`, `dragontag/app/tagging/writers/mp4.py`, `dragontag/app/tagging/writers/__init__.py`, `dragontag/app/config.py`, `dragontag/app/identify/acoustid.py`, `dragontag/app/identify/musicbrainz.py`, `dragontag/app/ingest/pipeline.py`, `dragontag/app/main.py`, `dragontag/app/web/templates/settings.html`, `tests/test_schema_vorbis.py`
