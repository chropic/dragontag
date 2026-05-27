<div align="center">

# 🐉 dragontag

**Self-hosted, Docker-native music tagger and library organizer**

[![CI](https://github.com/chropic/dragontag/actions/workflows/ci.yml/badge.svg)](https://github.com/chropic/dragontag/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)

Drop an audio file. Get a perfectly tagged, organized result — automatically.

</div>

---

dragontag identifies every file against **MusicBrainz** (with optional **AcoustID** acoustic fingerprinting), writes a complete, casing-exact Vorbis-style tag set, embeds cover art from the Cover Art Archive, fetches synced lyrics from LRCLIB, and moves the file into a clean `Artist / Album / 01. Title.flac` library layout.

High-confidence matches flow through completely hands-free. Everything else lands in a review queue where you pick the right candidate, resolve destination conflicts, or override a missing `RELEASETYPE` — all from the browser.

---

## Features

| | |
|---|---|
| **Drop & forget ingest** | Drag-and-drop in the web UI *or* drop files into the watched folder — both hit the same pipeline |
| **MusicBrainz-first ID** | Short-circuits on an existing `MUSICBRAINZ_TRACKID`; otherwise searches by title / artist / album / duration |
| **AcoustID fingerprint fallback** | Toggleable. Uses `fpcalc` (bundled in the image) when text search comes up empty |
| **Confidence-scored auto-apply** | Matches above the threshold are tagged and moved without human intervention |
| **Review queue** | Low-score matches, missing `RELEASETYPE`, and destination conflicts surface a candidate picker and action buttons |
| **Format coverage** | FLAC · MP3 (ID3v2.4) · WAV (ID3 chunk) · M4A / MP4 |
| **Cover art** | Best available resolution from the Cover Art Archive, embedded in the file *and* written as `cover.jpg` |
| **Lyrics + advisory** | Synced LRC or plain text from LRCLIB, embedded per-format; explicit content auto-tagged as `ITUNESADVISORY` |
| **Dry-run mode** | Preview destination paths and assembled tags without touching any files |
| **Webhook notifications** | Discord-compatible webhook fires on job completion or error |
| **Paginated dashboard** | Live-refreshing job list with inline log expansion, pagination, and one-click re-queue |
| **SQLite-backed state** | All jobs and history survive container restarts |
| **First-run wizard** | Set credentials and AcoustID key from the browser on first boot — no Docker secrets required |

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

# 3. Point /library and /drop at your actual paths
$EDITOR docker-compose.yml

# 4. The container runs as uid 1000 — make sure your paths are writable
sudo chown -R 1000:1000 /srv/music/library /srv/music/drop ./config

# 5. Pull and start
docker compose up -d
```

Open **http://localhost:7593** and log in. The first boot redirects you to `/setup` if no password is configured yet.

> **Building locally** — swap `image:` for `build: .` in `docker-compose.yml`.

---

## Configuration

### Volumes

| Mount | Contents |
|---|---|
| `/library` | Destination root — files land at `Artist/Album/[Disc N/]NN. Title.ext` |
| `/drop` | Watched ingest folder — anything dropped here is queued automatically |
| `/config` | SQLite DB (`dragontag.db`), `settings.json`, password hash, AcoustID key |

### Environment variables

| Variable | Purpose |
|---|---|
| `AIO_USERNAME` | Web UI login username (default `admin`) |
| `AIO_PASSWORD_FILE` | Path to argon2-hashed password file (Docker secret recommended) |
| `AIO_SESSION_SECRET_FILE` | Session signing secret. Falls back to an ephemeral random value |
| `AIO_ACOUSTID_KEY_FILE` | Path to AcoustID API key file. Optional |
| `AIO_LIBRARY_PATH` | Override default `/library` mount |
| `AIO_DROP_PATH` | Override default `/drop` mount |
| `AIO_CONFIG_PATH` | Override default `/config` mount |

### Settings UI

Everything below is editable live from the **Settings** page and written atomically to `/config/settings.json`:

- AcoustID fingerprint fallback on/off
- Auto-apply confidence threshold (default `0.85`)
- Filename templates — single-disc and multi-disc variants with `{track}`, `{disc}`, `{title}`, `{artist}`, `{ext}` vars
- Per-tag multi-value separators (`ARTIST`, `album_artist`, `ARTISTS`, `GENRE`, …)
- Genre limit and casing (`Title Case` / `lowercase` / `as-is`)
- Fields to skip entirely (suppressed at write time across all formats)
- Watcher on/off, ignore patterns, settle window
- Cover-art minimum pixel width before overwriting an existing `cover.jpg`
- Discord webhook URL, `on_done` and `on_error` toggles
- Dry-run mode toggle
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

Files sent to the review queue show the top 5 MB candidates with scores and links. Destination conflicts get **replace / rename / skip** buttons.

---

## Tag convention

The canonical schema is defined in [`schema.py`](dragontag/app/tagging/schema.py). The default Vorbis-Comment shape (FLAC):

| Tag | Value |
|---|---|
| `TITLE` | MB recording title |
| `ARTIST` | MB artist-credit phrase — multi-value joined with `//` |
| `ARTISTS` | MB artist names — joined with `;` |
| `ARTISTSORT` | MB artist sort names |
| `ALBUM` | MB release title |
| `album_artist` *(lowercase)* | MB release artist-credit phrase |
| `ALBUMARTISTSORT` | MB release artist sort names |
| `COMPOSER` · `LYRICIST` · `ARRANGER` · `CONDUCTOR` | From MB recording / work relations |
| `DATE` · `ORIGINALDATE` · `ORIGINALYEAR` | Release date + release-group first-release-date |
| `track` *(lowercase)* | `NN/TT` — also written as `TRACKTOTAL` + `TOTALTRACKS` |
| `disc` *(lowercase)* | `N/T` — also written as `DISCTOTAL` + `TOTALDISCS` |
| `GENRE` | Top community-voted MB tags |
| `LABEL` · `MEDIA` · `BARCODE` · `ISRC` | From MB release / recording |
| `RELEASECOUNTRY` · `RELEASESTATUS` · `RELEASETYPE` · `SCRIPT` | From MB release / release-group |
| `LYRICS` | Synced LRC or plain text from LRCLIB |
| `ITUNESADVISORY` | `0` clean · `1` explicit (auto-classified from lyrics) |
| `ACOUSTID_ID` | From AcoustID match or pre-existing tag |
| `MUSICBRAINZ_TRACKID` · `_RELEASETRACKID` · `_ALBUMID` · `_ALBUMARTISTID` · `_ARTISTID` · `_RELEASEGROUPID` | Full MB provenance |

**Other formats** map this schema into their native containers:

- **MP3 / WAV** — ID3v2.4 standard frames (`TIT2`, `TPE1`, `TALB`, `TRCK`, …) + `TXXX:NAME` for non-standard fields
- **M4A / MP4** — standard atoms (`©nam`, `©ART`, `aART`, `trkn`, `disk`, …) + `----:com.apple.iTunes:NAME` freeform atoms

---

## Development

```bash
git clone https://github.com/chropic/dragontag.git
cd dragontag

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run all tests
pytest -v

# Run the dev server against local paths
AIO_LIBRARY_PATH=./library AIO_DROP_PATH=./drop AIO_CONFIG_PATH=./config \
AIO_USERNAME=dev AIO_PASSWORD=dev \
uvicorn dragontag.app.main:app --reload --port 7593
```

### Project layout

```
dragontag/app/
├── main.py               FastAPI routes + HTMX wiring
├── config.py             Env vars · Docker secrets · settings.json layers
├── db.py                 SQLite engine bootstrap (SQLModel)
├── models.py             Job · Track · LibraryFolder · enums
├── auth.py               argon2 verify + session helpers
├── notify.py             Discord webhook sender (fire-and-forget)
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
    └── organizer.py       Reorganize library by current filename template
```

### Tests

```bash
pytest -v
```

Pure-logic, no-network tests cover the most failure-prone paths:

| File | What it checks |
|---|---|
| `test_paths.py` | `sanitize_segment` strips only forbidden chars; correct single- and multi-disc destination paths |
| `test_schema_vorbis.py` | `TrackTags.to_vorbis()` output matches the reference Vorbis field-for-field, including exact casing |
| `test_scoring.py` | Perfect match scores high; wrong title scores low |
| `test_lyrics_advisory.py` | Lyrics embedded correctly per format; explicit classifier fires on known words, respects word boundaries |

---

## License

MIT — see [LICENSE](LICENSE).
