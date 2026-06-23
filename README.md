<div align="center">

# 🐉 dragontag

**Self-hosted music tagger and library organizer for Docker**

[![CI](https://github.com/chropic/dragontag/actions/workflows/ci.yml/badge.svg)](https://github.com/chropic/dragontag/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)

Drop an audio file. Get a perfectly tagged, organized result — automatically.

</div>

---

dragontag identifies every file against **MusicBrainz** (with optional **AcoustID** fingerprinting), writes a complete, casing-exact tag set, embeds cover art from the Cover Art Archive, fetches synced lyrics from LRCLIB, and moves the file into a clean `Artist / Album / 01. Title.flac` structure.

High-confidence matches flow through hands-free. Low-confidence files land in a review queue where you pick the right candidate, resolve conflicts, or fill in missing fields — all from the browser.

---

## Features

### Identification

| Feature | Description |
|---|---|
| **MusicBrainz-first** | Short-circuits on an existing `MUSICBRAINZ_TRACKID`; otherwise searches by title / artist / album / duration with progressive fallback to maximise hit rate |
| **AcoustID fingerprint** | Toggleable acoustic fingerprint fallback using `fpcalc` (bundled in the image) when text search comes up empty |
| **Confidence scoring** | Matches above the threshold are tagged and moved automatically; everything else goes to review |

### Tagging & metadata

| Feature | Description |
|---|---|
| **Format coverage** | FLAC · MP3 (ID3v2.4) · WAV (ID3 chunk) · M4A / MP4 |
| **Cover art** | Best available resolution from Cover Art Archive, resized to ≤ 1200 px, embedded in the file *and* written as `cover.jpg` |
| **Lyrics** | Synced LRC or plain text from LRCLIB, embedded per-format |
| **Advisory tagging** | Explicit content auto-classified from lyrics and written as `ITUNESADVISORY` |
| **Smart formatting** | Title Case, qualifier parenthesization (`Song Live` → `Song (Live)`), grammar fixes (ALL-CAPS, contractions, possessives) |
| **Genre filter** | MB community tags filtered against a ~1500-entry canonical genre list — kills noise like "billboard top 100"; clean tags survive as a fallback |

### Library management

| Feature | Description |
|---|---|
| **Organize** | Moves all tracks to their canonical paths based on current filename templates; picks up manual edits after a scan |
| **Library scan** | Indexes existing on-disk files into the DB — useful after editing tags outside dragontag |
| **Batch operations** | One-click chained runs: **Organize batch** (organize + fix disc folders + normalize + covers + prune + dedupe + find missing), **Re-tag batch** (validate + advisories + ReplayGain + full pipeline), and the **Nuclear option** (both) |
| **Library actions** | 12 individual actions (fetch lyrics/covers, extract covers, ReplayGain, verify integrity, validate tags, fix disc folders, normalize filenames, find duplicates, prune junk, find missing tracks, tag advisories) — run one or multi-select to chain |
| **Incomplete albums** | Persisted results of "find missing tracks": albums with fewer local tracks than the MB total, with missing titles, MB links, and per-row dismiss |
| **Library table** | Column sorting and pagination (10 / 25 / 50 / 100 / 200); explicit advisory badge on each row |

### Workflow

| Feature | Description |
|---|---|
| **Drop & ingest** | Drag-and-drop in the browser or drop files into the watched folder — both hit the same pipeline |
| **Review queue** | Low-confidence matches, missing `RELEASETYPE`, and destination conflicts surface a candidate picker with scores and links, manual MB search, and action buttons |
| **Dry-run mode** | Preview destination paths and assembled tags without touching any files — global setting plus per-run overrides on Library actions |
| **Change history** | Every tag-write is recorded; revert a file's tags in place or move it back to its original directory with one click |

### Automation & notifications

| Feature | Description |
|---|---|
| **Scheduling** | Standard cron expressions for scans, organizes, batches, lyrics/cover fetches, and backups — with run-now, next-run display, and live plain-English descriptions |
| **Webhooks** | Discord-compatible webhook on job completion or error |
| **Universal progress bar** | Live progress line under the nav on every page: percentage, item counts, and current file |

### System

| Feature | Description |
|---|---|
| **Scan filters** | Regex patterns (by filename), excluded directories, and excluded files — all applied to the watcher, scanner, and bulk re-tag; clearable with one click |
| **Backup / restore** | Versioned tarball of the DB, settings, password hash, and AcoustID key; restore from the UI or the `restore_backup` CLI |
| **Change retention** | Configurable cap on audit-log rows (`0` = unlimited) |
| **First-run wizard** | Set credentials and AcoustID key from the browser on first boot |
| **SQLite-backed** | All jobs and history survive container restarts |
| **API docs** | Auth-guarded Swagger UI at `/api-docs` and raw schema at `/openapi.json` |

---

## Quick start

```bash
git clone https://github.com/chropic/dragontag.git
cd dragontag

# 1. Hash a password for the web UI
mkdir -p secrets config
python -m dragontag.tools.hash_password 'your-password' > secrets/password.txt

# 2. (Optional) AcoustID key for fingerprint fallback
echo 'your-acoustid-key' > secrets/acoustid_key.txt

# 3. Point /library and /drop at your actual paths, then start
$EDITOR docker-compose.yml
docker compose up -d
```

Open **http://localhost:7593** and log in. First boot redirects to `/setup` if no password is configured yet.

> **Building locally** — swap `image:` for `build: .` in `docker-compose.yml`.

---

## Configuration

### Volumes

| Mount | Contents |
|---|---|
| `/library` | Destination root — files land at `Album Artist/Album/[Disc N/]NN. Title.ext` |
| `/drop` | Watched ingest folder — files dropped here are queued automatically |
| `/config` | SQLite DB, `settings.json`, password hash, AcoustID key |

### Environment variables

| Variable | Purpose |
|---|---|
| `DRAGONTAG_USERNAME` | Web UI login (default `admin`) |
| `DRAGONTAG_PASSWORD_FILE` | Path to argon2-hashed password file (Docker secret recommended) |
| `DRAGONTAG_PASSWORD` | Plain-text password — dev/testing only |
| `DRAGONTAG_SESSION_SECRET_FILE` | Session signing secret; falls back to an ephemeral value |
| `DRAGONTAG_ACOUSTID_KEY_FILE` | Path to AcoustID API key file |
| `DRAGONTAG_LIBRARY_PATH` | Override `/library` mount |
| `DRAGONTAG_DROP_PATH` | Override `/drop` mount |
| `DRAGONTAG_CONFIG_PATH` | Override `/config` mount |
| `TZ` | Timezone for displayed timestamps, e.g. `America/New_York` |

> **Migration note:** Variables were renamed from the `AIO_` prefix to `DRAGONTAG_`. Update your `docker-compose.yml` accordingly.

### Settings UI

The **Settings** page covers everything below — changes are written atomically to `/config/settings.json`:

- AcoustID fingerprint on/off, auto-apply confidence threshold
- Network timeout for outbound MusicBrainz/AcoustID calls (default 15s, so a stalled connection can't wedge the worker)
- Filename templates (single-disc and multi-disc) and artist-folder split separators
- Per-tag multi-value separators (`ARTIST`, `GENRE`, `LABEL`, …)
- Genre limit, casing, and canonical-list junk filter
- Fields to skip at write time across all formats
- Watcher on/off, ignore patterns, settle window (a file is ingested only once its size stops changing, so partial SMB/NFS transfers aren't read half-written)
- Cover-art minimum width and release-group fallback toggle
- Discord webhook URL and on/off per event type
- Dry-run mode, scan filters, change-retention cap, log verbosity (0–4)
- Backup download and validated restore
- MusicBrainz user-agent and server (for self-hosted mirrors)

---

## How it works

```
  Drop file / upload
        │
        ▼
  ┌─────────────────┐
  │  Watcher / UI   │──── enqueue ────► Job row (SQLite)
  └─────────────────┘                        │
                                             ▼
                          ┌──────────────────────────────────────┐
                          │  Pipeline                             │
                          │                                       │
                          │  1. Read existing tags + filename     │
                          │  2. MBID short-circuit (if present)   │
                          │     else  MusicBrainz text search     │
                          │     else  AcoustID fingerprint        │
                          │                                       │
                          │  3. Score top candidate               │
                          │     ≥ threshold ──► auto-apply        │
                          │     < threshold ──► review queue      │
                          │                                       │
                          │  4. Assemble TrackTags from MB        │
                          │  5. Fetch cover art (CAA)             │
                          │  6. Fetch lyrics (LRCLIB)             │
                          │  7. Write tags (format-aware)         │
                          │  8. Move into library + cover.jpg     │
                          │  9. Fire webhook (if configured)      │
                          └──────────────────────────────────────┘
```

Files in the review queue show the top 5 MB candidates with scores and links. Destination conflicts get **replace / rename / skip** buttons.

### Data-integrity guarantees

- **Atomic tag writes** — every tag write (full re-tag, single-field updates, and revert) is performed on a temp copy in the same directory and then atomically swapped in. A crash mid-write can only ever damage the throwaway temp, never your original audio file. Cover-art sidecars (`cover.jpg`) are written the same way.
- **Verified moves** — a file move confirms the destination's byte count matches the source, catching a silently-truncated write over a flaky network volume.
- **Stalled-task recovery** — a background task with no progress heartbeat for 15 minutes is reaped to `error` so a hung run can't block future scheduled work; bounded network timeouts keep the single worker from hanging in the first place.

---

## Tag reference

dragontag writes a rich, source-traceable tag set — every field comes directly from MusicBrainz or one of its associated services. Nothing is inferred or guessed. The Vorbis Comment field names below are used for FLAC; MP3, WAV, and M4A map these to their native containers (ID3v2.4 frames and `TXXX` for non-standard fields; M4A atoms and `----:com.apple.iTunes:` freeform atoms).

The canonical schema lives in [`schema.py`](dragontag/app/tagging/schema.py).

| Field | What's stored |
|---|---|
| `TITLE` | Track title, exactly as MusicBrainz records it |
| `ARTIST` | Performing artist(s) — one value per artist (native multi-value) |
| `ARTISTS` | Same as `ARTIST`; written separately for players that prefer this field |
| `ARTISTSORT` | Artist sort names from MB (e.g. "Bowie, David") |
| `ALBUM` | Release title |
| `album_artist` | Album-level artist(s) — the name the folder is grouped under |
| `ALBUMARTISTSORT` | Album artist sort names |
| `COMPOSER` · `LYRICIST` · `ARRANGER` · `CONDUCTOR` | From MB recording and work relationships |
| `DATE` | Release date |
| `ORIGINALDATE` · `ORIGINALYEAR` | First release date of the release group (the original year the album came out, not this specific edition) |
| `track` | Track number as `NN/TT`; also written as `TRACKTOTAL` and `TOTALTRACKS` |
| `disc` | Disc number as `N/T`; also written as `DISCTOTAL` and `TOTALDISCS` |
| `GENRE` | Top community-voted MB tags, filtered for quality |
| `LABEL` · `MEDIA` · `BARCODE` · `ISRC` | Label, format, barcode, and recording ISRC from MB |
| `RELEASECOUNTRY` · `RELEASESTATUS` · `RELEASETYPE` · `SCRIPT` | Release metadata from MB |
| `LYRICS` | Synced `.lrc` or plain text from LRCLIB |
| `ITUNESADVISORY` | `0` = clean · `1` = explicit (auto-classified from lyrics) |
| `ACOUSTID_ID` | AcoustID fingerprint match, or carried over from a pre-existing tag |
| `MUSICBRAINZ_TRACKID` | MB recording ID — the primary link back to the source |
| `MUSICBRAINZ_RELEASETRACKID` · `_ALBUMID` · `_ALBUMARTISTID` · `_ARTISTID` · `_RELEASEGROUPID` | Full MB provenance trail |
| `TAGGER` | `tagged via dragontag/x.y.z` |

---

## Development

```bash
git clone https://github.com/chropic/dragontag.git
cd dragontag

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run tests
pytest -v

# Dev server
DRAGONTAG_LIBRARY_PATH=./library DRAGONTAG_DROP_PATH=./drop \
DRAGONTAG_CONFIG_PATH=./config DRAGONTAG_USERNAME=dev DRAGONTAG_PASSWORD=dev \
uvicorn dragontag.app.main:app --reload --port 7593
```

### Front-end

The UI is a self-hosted terminal/TUI design (JetBrains Mono, monochrome, `.dt-*` panel
primitives) — no CDN. The stylesheet is compiled ahead of time and committed:

```bash
# After editing templates or frontend/app.input.css, rebuild dragontag/app/web/static/app.css
bash frontend/build_css.sh
```

See `frontend/README.md` for the `.dt-*` component layer and font details.

### Database migrations

```bash
# After changing models.py
DRAGONTAG_CONFIG_PATH=./config alembic revision --autogenerate -m "describe change"

# Apply
DRAGONTAG_CONFIG_PATH=./config alembic upgrade head
```

### Project layout

```
dragontag/app/
├── main.py               FastAPI routes + HTMX wiring
├── config.py             Env vars · Docker secrets · settings.json layers
├── db.py                 SQLite engine bootstrap (SQLModel)
├── models.py             Job · Track · LibraryFolder · ScheduledTask · FileChange · enums
├── auth.py               argon2 verify + session helpers
├── notify.py             Discord webhook sender (fire-and-forget)
├── tasks.py              Background task runner (jobs with kind/progress/log)
├── scheduler.py          Cron scheduler (croniter) dispatching tasks
├── backup.py             Versioned backup tarball + validated restore
├── logsetup.py           Runtime 0–4 log-verbosity application
├── ingest/
│   ├── pipeline.py       Per-file orchestration + background worker queue
│   ├── watcher.py        watchdog observer with settle window
│   ├── uploads.py        UI upload handler
│   └── bulk.py           Folder-level bulk re-tag enqueuer
├── identify/
│   ├── existing_tags.py  mutagen-based normalized tag reader
│   ├── filename_parse.py "Artist - Title" / "NN - Title" heuristics
│   ├── musicbrainz.py    musicbrainzngs search + TrackTags assembler
│   ├── acoustid.py       fpcalc + AcoustID lookup
│   └── scoring.py        Confidence model (title / artist / album / duration)
├── tagging/
│   ├── schema.py         TrackTags dataclass + Vorbis rendering
│   ├── coverart.py       Cover Art Archive fetcher
│   ├── lyrics_fetcher.py LRCLIB client (synced + plain text)
│   ├── advisory.py       Explicit-content classifier
│   └── writers/          Format dispatch → flac · mp3 · mp4 · wav
└── library/
    ├── paths.py           sanitize_segment + build_destination
    ├── mover.py           Move with conflict detection + cover.jpg writer
    ├── scanner.py         Index existing files into Track table
    ├── organizer.py       Reorganize library by current filename template
    ├── actions.py         Individual library actions (lyrics, covers, replaygain, …)
    ├── filters.py         Scan filter helper (regex patterns + dir/file exclusions)
    └── revert.py          Undo a recorded FileChange / move a file back
```

### Tests

Pure-logic, no-network tests cover the most failure-prone paths:

| File | What it checks |
|---|---|
| `test_paths.py` | `sanitize_segment` strips only forbidden chars; correct single- and multi-disc destination paths |
| `test_schema_vorbis.py` | `TrackTags.to_vorbis()` matches the reference field-for-field, including exact casing and native multi-value lists |
| `test_writers_multivalue.py` | WAV/ID3 round-trip writes multi-value ARTIST/ALBUMARTIST/GENRE as separate values |
| `test_snapshot.py` | Revert snapshot captures then restores a file's original tags |
| `test_atomic_writes.py` | A tag write injected to fail mid-save leaves the original byte-identical and leaves no temp behind |
| `test_scoring.py` | Perfect match scores high; wrong title scores low |
| `test_scoring_unicode.py` | Scores match across unicode forms (NFC/NFD) and casing; a 0-second duration still participates |
| `test_musicbrainz_credits.py` | Artist-credit extraction tolerates malformed/partial MB payloads without raising |
| `test_existing_tags_corrupt.py` | A corrupt/unreadable file degrades to empty clues instead of erroring the job |
| `test_watcher_settle.py` | A file is released for ingest only once its size is stable across the settle window |
| `test_tasks_reaper.py` | Heartbeat-stale `running` jobs are reaped to `error`; fresh ones are left alone |
| `test_mover_verify.py` | `samefile` survives a vanished source; truncated moves are detected; cover writes are atomic |
| `test_lyrics_advisory.py` | Lyrics embedded correctly per format; explicit classifier fires on known words, respects word boundaries |
| `test_scan_filters.py` | Regex patterns, directory exclusions, and file exclusions all filter correctly |

---

[MIT License](LICENSE)
