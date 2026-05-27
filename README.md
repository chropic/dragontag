# dragontag

![CI](https://github.com/chropic/dragontag/actions/workflows/ci.yml/badge.svg)

A self-hosted, Docker-deployable music tagger and library organizer.

Drop audio files into a watched folder (or upload them through the web UI). The app
identifies each file against **MusicBrainz** (with optional **AcoustID** fingerprinting),
writes a full set of tags in a customizable Vorbis-Comment-style convention, embeds
high-quality cover art, and moves the file into a tidy
`Library/Artist/Album/[Disc N/]Filename.ext` layout.

Built for users who want MusicBrainz Picard's accuracy without having to run it
manually on every new file.

---

## Features

- **Drop & forget ingest** — drag-and-drop in the web UI *or* drop files into the
  watched folder; both flow through the same pipeline.
- **MusicBrainz-first identification** — short-circuits on an existing
  `MUSICBRAINZ_TRACKID`, otherwise searches by title/artist/album/duration.
- **AcoustID fingerprint fallback** — toggleable in settings. Uses `fpcalc` (bundled
  in the Docker image) when text search fails.
- **Confidence-scored auto-apply** — high-confidence matches are tagged & moved
  automatically; uncertain matches and destination conflicts land in a review queue.
- **Customizable tag convention** — per-tag multi-value separators (e.g. `ARTIST=//`,
  `ARTISTS=;`), exact-casing Vorbis fields (`album_artist`, `track`, `disc` lowercase
  to match the user's reference convention), duplicated track/disc totals
  (`TRACKTOTAL` + `TOTALTRACKS`, `DISCTOTAL` + `TOTALDISCS`).
- **Format coverage** — FLAC (Vorbis Comments), MP3 (ID3v2.4), WAV (ID3 chunk),
  M4A/MP4 (atoms + freeform `----:com.apple.iTunes:` for MB IDs).
- **Cover art** — fetched at the best available resolution from the Cover Art Archive,
  embedded in the file *and* written as `cover.jpg` next to it.
- **Docker-native** — `/library`, `/drop`, `/config` volumes; password and AcoustID
  key are loaded from Docker secret files (argon2-hashed).
- **Web UI** — dashboard with live-refreshing job list, review queue with candidate
  picker + `RELEASETYPE` override, conflict resolver, settings page.
- **SQLite-backed state** — jobs and history survive container restarts.

---

## Quick start (Docker)

```bash
git clone https://github.com/chropic/dragontag.git
cd dragontag

# 1. Hash a password and stash it as a Docker secret
mkdir -p secrets config
python -m dragontag.tools.hash_password 'your-password' > secrets/password.txt

# 2. (Optional) drop your AcoustID API key in:
echo 'your-acoustid-key' > secrets/acoustid_key.txt

# 3. Edit docker-compose.yml — point /library and /drop at your actual paths
$EDITOR docker-compose.yml

# 4. The container runs as uid 1000; make sure your host paths are writable by it:
sudo chown -R 1000:1000 /srv/music/library /srv/music/drop ./config

# 5. Up (pulls the published image from GHCR automatically)
docker compose up -d
```

Open <http://localhost:7593>, log in with the username from `AIO_USERNAME` (default
`charlie` in the sample compose file) and the password you hashed.

> **Building locally** — replace the `image:` line in `docker-compose.yml` with
> `build: .` to build from source instead of pulling.

### Volumes

| Mount | Purpose |
|---|---|
| `/library` | Destination library root. Files land at `Library/Artist/Album/[Disc N/]<filename>`. |
| `/drop`    | Watched ingest folder. Anything dropped here gets queued automatically. |
| `/config`  | SQLite DB (`dragontag.db`), `settings.json`, logs. |

### Environment variables

| Var | Purpose |
|---|---|
| `AIO_USERNAME` | Web UI login username. |
| `AIO_PASSWORD_FILE` | Path to argon2-hashed password (use Docker secret). |
| `AIO_SESSION_SECRET_FILE` | Path to session signing secret. Defaults to ephemeral random. |
| `AIO_ACOUSTID_KEY_FILE` | Path to AcoustID key file. Optional. |
| `AIO_LIBRARY_PATH` / `AIO_DROP_PATH` / `AIO_CONFIG_PATH` | Override default mount points. |

---

## How it works

```
              ┌───────────────────┐
  Drop file ──►   Watcher / UI    │── enqueue ──►  Job (SQLite)
              └───────────────────┘                    │
                                                       ▼
                                   ┌─────────────────────────────────────┐
                                   │  Pipeline                            │
                                   │   1. Read existing tags + filename   │
                                   │   2. Direct MBID short-circuit       │
                                   │      else MusicBrainz text search    │
                                   │      else AcoustID fingerprint       │
                                   │   3. Score top candidate             │
                                   │   4. ≥ threshold → auto              │
                                   │      < threshold → review queue      │
                                   │   5. Assemble TrackTags from MB      │
                                   │   6. Fetch cover (CAA, highest res)  │
                                   │   7. Write tags (format-aware)       │
                                   │   8. Move into library + cover.jpg   │
                                   └─────────────────────────────────────┘
```

When a file is sent to the review queue, the UI shows the top 5 candidates with
their scores, MusicBrainz links, and a `RELEASETYPE` override dropdown. Destination
conflicts get **replace / rename / skip** buttons.

---

## Tag convention

The default Vorbis-Comment shape (FLAC) matches this exact layout — see
[`schema.py`](dragontag/app/tagging/schema.py):

| Tag | Source / Notes |
|---|---|
| `TITLE` | MB recording title |
| `ARTIST` | MB artist-credit phrase, multi-value joined with `//` (configurable) |
| `ARTISTS` | MB artist names, joined with `;` |
| `ARTISTSORT` | MB artist sort names |
| `ALBUM` | MB release title |
| `album_artist` *(lowercase)* | MB release artist-credit phrase |
| `ALBUMARTISTSORT` | MB release artist sort names |
| `COMPOSER` | MB recording → work-relations of type `composer` |
| `DATE` / `ORIGINALDATE` / `ORIGINALYEAR` | MB release date + release-group first-release-date |
| `track` *(lowercase)* | `NN/TT` format |
| `TRACKTOTAL` + `TOTALTRACKS` | Both written for compatibility |
| `disc` *(lowercase)* + `DISCTOTAL` + `TOTALDISCS` | Same |
| `GENRE` | Top MB tags |
| `LABEL`, `MEDIA`, `BARCODE`, `ISRC` | From MB release / recording |
| `RELEASECOUNTRY`, `RELEASESTATUS`, `RELEASETYPE`, `SCRIPT` | From MB release / release-group |
| `ACOUSTID_ID` | From AcoustID match or pre-existing tag |
| `MUSICBRAINZ_TRACKID` / `…_RELEASETRACKID` / `…_ALBUMID` / `…_ALBUMARTISTID` / `…_ARTISTID` / `…_RELEASEGROUPID` | Full MB integration |

Lyrics and `ITUNESADVISORY` are intentionally skipped.

Other formats map this canonical schema into their native tags:

- **MP3 / WAV** — ID3v2.4 standard frames (`TIT2`, `TPE1`, `TPE2`, `TALB`,
  `TRCK`, `TPOS`, `TDRC`, …) plus `TXXX:NAME` frames for everything non-standard
  (`MUSICBRAINZ_*`, `RELEASETYPE`, `BARCODE`, etc.).
- **M4A / MP4** — standard atoms (`©nam`, `©ART`, `aART`, `trkn`, `disk`, …)
  plus `----:com.apple.iTunes:NAME` freeform atoms for MB and custom fields.

---

## Settings

All editable from the **Settings** page (and persisted to `/config/settings.json`):

- AcoustID enabled (bool)
- Auto-apply score threshold (default `0.85`)
- Per-tag multi-value separators (`ARTIST`, `album_artist`, `ARTISTS`, `GENRE`, …)
- Filename templates (single-disc, multi-disc) — vars: `{track}`, `{disc}`, `{title}`,
  `{artist}`, `{ext}`, `{disctotal}`, `{tracktotal}`
- Multi-disc folder name template (`Disc {disc}` by default)
- Watcher on/off, ignore patterns, settle time
- Cover-art minimum pixel width before overwriting an existing `cover.jpg`
- MusicBrainz user-agent string and server (for self-hosted MB mirrors)

---

## Development

```bash
git clone https://github.com/<you>/dragontag.git
cd dragontag
python -m venv .venv
source .venv/bin/activate            # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Run tests
pytest

# Run the server against ad-hoc local paths
AIO_LIBRARY_PATH=./library AIO_DROP_PATH=./drop AIO_CONFIG_PATH=./config \
AIO_USERNAME=dev AIO_PASSWORD='dev' \
uvicorn dragontag.app.main:app --reload --port 7593
```

### Project layout

```
dragontag/
  app/
    main.py                FastAPI routes + HTMX wiring
    config.py              Env + secret-file + JSON settings layers
    db.py                  SQLite engine bootstrap (SQLModel)
    models.py              Job / JobStatus / ReviewReason
    auth.py                argon2 verify + session helpers
    ingest/
      pipeline.py          Per-file orchestration; background queue
      watcher.py           watchdog observer with settle window
      uploads.py           UI upload handler
    identify/
      existing_tags.py     mutagen-based normalized tag reader
      filename_parse.py    "Artist - Title" / "NN - Title" heuristics
      musicbrainz.py       musicbrainzngs search + assemble TrackTags
      acoustid.py          fpcalc + AcoustID lookup
      scoring.py           Confidence model (title/artist/album/duration)
    tagging/
      schema.py            TrackTags dataclass + Vorbis rendering
      coverart.py          Cover Art Archive fetcher
      writers/
        __init__.py        Dispatch by extension
        _id3common.py      Shared ID3 frame builder (MP3 + WAV)
        flac.py / mp3.py / mp4.py / wav.py
    library/
      paths.py             sanitize_segment + build_destination
      mover.py             Move w/ conflict detection, cover.jpg writer
    web/
      templates/           Jinja2: login, dashboard, review, settings, job detail
      static/
  tools/
    hash_password.py       CLI for argon2 password hashing
tests/
  test_paths.py            Sanitization + destination assembly
  test_schema_vorbis.py    Vorbis output matches reference convention
  test_scoring.py          Confidence model sanity checks
```

### Tests

Pure-logic tests (no network, no audio I/O) cover the most failure-prone parts:

- `test_paths.py` — `sanitize_segment` strips only forbidden chars and trims trailing
  dots/spaces (Windows-safe); builds the expected
  `Bladee/gluee/01. deletee (intro).flac` path; multi-disc adds a `Disc N` subfolder.
- `test_schema_vorbis.py` — feeds the exact reference values from the user's
  `flac_metadata.md` into `TrackTags.to_vorbis()` and asserts every output key/value
  field-for-field, including casing.
- `test_scoring.py` — verifies a perfect match scores high and a wrong title
  scores low.

```bash
pytest -v
```

---

## Roadmap

- [x] Per-job cover-art picker — choose from MB candidates or upload a custom image
- [x] Dry-run mode — previews destination & tags without writing or moving
- [x] Bulk operations — re-tag everything from a given source folder
- [x] Lyrics + `ITUNESADVISORY` fetched from LRCLIB, toggleable in settings
- [ ] Webhook / Discord notifications for completed batches

---

## License

MIT. See [LICENSE](LICENSE).
