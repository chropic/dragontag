"""FastAPI entry point.

Wires up the session middleware, mounts the templates/static dirs, kicks off
the worker thread + watcher on startup, and defines the route handlers
backing the HTMX-driven UI.

Routes are grouped:

* ``/login`` / ``/logout``           — argon2-backed session auth
* ``/`` and ``/jobs/...``            — dashboard and per-job detail
* ``/upload``                        — multipart file upload, kicks pipeline
* ``/review`` and ``/review/...``    — review queue (candidate picker,
                                       conflict resolver)
* ``/settings``                      — UI-editable runtime settings
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .config import env, settings, store
from .db import dashboard_stats, session
from .identify import musicbrainz as mbq
from .ingest import pipeline, uploads, watcher
from .library.mover import move
from .library.paths import unique_path
from .models import Job, JobStatus, LibraryFolder, ReviewReason, Track


def _local_tz() -> ZoneInfo:
    tz = os.environ.get("TZ", "UTC")
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("UTC")


def _format_local(dt: datetime | None) -> str:
    """Format a UTC datetime as a local-timezone string using the TZ env var."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_local_tz())
    return local.strftime("%Y-%m-%d %H:%M")


def _toast_response(redirect_url: str, message: str, level: str = "success") -> Response:
    """Return a redirect that also carries an HX-Trigger showToast header."""
    resp = RedirectResponse(redirect_url, status_code=303)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"message": message, "level": level}})
    return resp

logging.basicConfig(level=logging.INFO)
logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)

app = FastAPI(title="dragontag")

# Cookie-signing secret comes from the session-secret Docker secret. The
# middleware itself implements signed but unencrypted cookies — fine for
# storing only the username; never put sensitive data in the session.
app.add_middleware(
    SessionMiddleware,
    secret_key=env().resolve_session_secret(),
    https_only=False,
    max_age=86400 * 7,  # 7-day cookie lifetime
)

# Static dir is created on first import so the StaticFiles mount doesn't
# error on a fresh checkout (we don't ship any static assets yet).
_static_dir = Path(__file__).parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))
templates.env.globals["format_local"] = _format_local


@app.on_event("startup")
def _startup() -> None:
    """Initialize config + DB, start worker, resume in-flight jobs, start watcher."""
    store()                       # ensure /config and SQLite are ready
    pipeline.start_worker()
    pipeline.resubmit_pending()   # finish anything that was mid-flight at last shutdown
    if settings().watcher_enabled:
        watcher.start()


@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


def require_auth(request: Request) -> None:
    """FastAPI dependency: redirect unauthenticated users to /login (or /setup on first boot).

    Raising HTTPException with a 303 + Location header is the FastAPI-idiomatic
    way to do a "stop processing this handler" redirect from a dependency.
    """
    if env().resolve_password() is None:
        raise HTTPException(status_code=303, headers={"Location": "/setup"})
    if not auth.is_authenticated(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if env().resolve_password() is None:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if env().resolve_password() is None:
        return RedirectResponse("/setup", status_code=303)
    if username == env().username and auth.verify(password):
        auth.login(request, username)
        return RedirectResponse("/", status_code=303)
    # Generic error — don't leak whether the username or password was wrong.
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": "Invalid credentials"},
        status_code=401,
    )


# ---------------------------------------------------------------------------
# First-run setup wizard
# ---------------------------------------------------------------------------


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    """Show the first-run wizard. Redirects away once a password is configured."""
    if env().resolve_password() is not None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"request": request, "error": None, "username": env().username})


@app.post("/setup")
def setup_submit(
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    acoustid_key: str = Form(default=""),
):
    """Persist the initial password (and optional AcoustID key) then redirect to login.

    Writes to the config volume so the credentials survive container restarts
    without needing Docker secrets pre-configured. Docker-secret paths always
    take priority over these files (see config.py resolve_password).
    """
    if env().resolve_password() is not None:
        return RedirectResponse("/login", status_code=303)

    error = None
    if not password:
        error = "Password cannot be empty."
    elif password != password_confirm:
        error = "Passwords do not match."

    if error:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": error, "username": env().username},
            status_code=422,
        )

    config_dir = env().config_path
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "password.hash").write_text(auth.hash_password(password), encoding="utf-8")

    if acoustid_key.strip():
        (config_dir / "acoustid.key").write_text(acoustid_key.strip(), encoding="utf-8")

    return RedirectResponse("/login", status_code=303)


@app.post("/logout")
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard + uploads
# ---------------------------------------------------------------------------


_PER_PAGE = 50


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _: None = Depends(require_auth), page: int = 1):
    page = max(1, page)
    with session() as s:
        total = s.exec(select(func.count(Job.id))).one()
        # Show only 5 recent jobs on the dashboard; the /jobs page shows all.
        recent_jobs = s.exec(
            select(Job).order_by(Job.updated_at.desc()).limit(5)
        ).all()
        # Library stats
        total_tracks = s.exec(select(func.count(Track.id))).one()
        total_albums = s.exec(select(func.count(func.distinct(Track.album)))).one()
        total_artists = s.exec(select(func.count(func.distinct(Track.artist)))).one()
    stats = dashboard_stats()
    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "jobs": recent_jobs,
        "total_jobs": total,
        "total_tracks": total_tracks,
        "total_albums": total_albums,
        "total_artists": total_artists,
        "stats": stats,
        "active_page": "dashboard",
    })


@app.get("/jobs/table", response_class=HTMLResponse)
def jobs_table(request: Request, _: None = Depends(require_auth), page: int = 1):
    """HTMX partial: just the table body, polled every 5s by the dashboard."""
    page = max(1, page)
    with session() as s:
        total = s.exec(select(func.count(Job.id))).one()
        jobs = s.exec(
            select(Job).order_by(Job.updated_at.desc())
            .offset((page - 1) * _PER_PAGE).limit(_PER_PAGE)
        ).all()
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    return templates.TemplateResponse(request, "_jobs_table.html", {
        "request": request, "jobs": jobs, "page": page, "total_pages": total_pages,
    })


@app.post("/upload")
async def upload(request: Request, _: None = Depends(require_auth), files: list[UploadFile] = []):
    await uploads.save_uploads(files)
    return RedirectResponse("/", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, _: None = Depends(require_auth), page: int = 1):
    """Full job queue page with all controls."""
    page = max(1, page)
    with session() as s:
        total = s.exec(select(func.count(Job.id))).one()
        jobs = s.exec(
            select(Job).order_by(Job.updated_at.desc())
            .offset((page - 1) * _PER_PAGE).limit(_PER_PAGE)
        ).all()
        pending = s.exec(select(func.count(Job.id)).where(Job.status.in_([
            JobStatus.queued, JobStatus.identifying, JobStatus.tagging, JobStatus.moving
        ]))).one()
        done_today = s.exec(select(func.count(Job.id)).where(Job.status == JobStatus.done)).one()
        errors = s.exec(select(func.count(Job.id)).where(Job.status == JobStatus.error)).one()
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    return templates.TemplateResponse(request, "jobs.html", {
        "request": request,
        "jobs": jobs,
        "page": page,
        "total_pages": total_pages,
        "pending": pending,
        "done_today": done_today,
        "errors": errors,
        "active_page": "jobs",
    })


@app.post("/jobs/clear-completed")
def jobs_clear_completed(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.done)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/jobs", f"Cleared {len(rows)} completed job(s).")


@app.post("/jobs/clear-errors")
def jobs_clear_errors(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.error)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/jobs", f"Cleared {len(rows)} error job(s).")


@app.post("/jobs/clear-all")
def jobs_clear_all(request: Request, _: None = Depends(require_auth)):
    """Delete every Job row that is not currently in-flight."""
    active = {JobStatus.queued, JobStatus.identifying, JobStatus.tagging, JobStatus.moving}
    with session() as s:
        rows = s.exec(select(Job).where(~Job.status.in_(active))).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/jobs", f"Cleared {len(rows)} job(s).")


@app.post("/jobs/clear-needs-review")
def jobs_clear_needs_review(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.needs_review)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/jobs", f"Cleared {len(rows)} needs-review job(s).")


@app.post("/jobs/cancel-queued")
def jobs_cancel_queued(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.queued)).all()
        for r in rows:
            r.status = JobStatus.skipped
            s.add(r)
        s.commit()
    return _toast_response("/jobs", f"Cancelled {len(rows)} queued job(s).")


@app.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job or job.status != JobStatus.queued:
            raise HTTPException(400, "only queued jobs can be cancelled")
        job.status = JobStatus.skipped
        s.add(job)
        s.commit()
    return RedirectResponse("/jobs", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
    return templates.TemplateResponse(request, "job_detail.html", {"request": request, "job": job, "active_page": "jobs"})


@app.post("/jobs/{job_id}/requeue")
def job_requeue(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        if job.status not in (JobStatus.done, JobStatus.error, JobStatus.skipped):
            raise HTTPException(400, "only done/error/skipped jobs can be requeued")
        job.status = JobStatus.queued
        job.error = None
        job.log = ""
        s.add(job)
        s.commit()
    pipeline.submit(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}/log", response_class=HTMLResponse)
def job_log(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
    text = job.log or job.error or "(no log)"
    return HTMLResponse(
        f'<pre class="text-xs text-[#8a8a8a] whitespace-pre-wrap p-2 m-0">{text}</pre>'
    )


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


@app.get("/review", response_class=HTMLResponse)
def review(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        items = s.exec(
            select(Job)
            .where(Job.status == JobStatus.needs_review)
            .order_by(Job.updated_at.desc())
        ).all()
    return templates.TemplateResponse(request, "review.html", {"request": request, "items": items, "active_page": "review"})


@app.post("/review/bulk-apply")
async def review_bulk_apply(
    request: Request,
    _: None = Depends(require_auth),
    job_ids: list[int] = Form(...),
):
    """Apply the top candidate for each selected review job."""
    applied = 0
    with session() as s:
        for job_id in job_ids:
            job = s.get(Job, job_id)
            if not job or job.status != JobStatus.needs_review:
                continue
            candidates = (job.candidates_json or {}).get("items", [])
            if not candidates:
                continue
            best = candidates[0]
            try:
                tags = mbq.assemble_tags(release_id=best["release_id"], recording_id=best["recording_id"])
                from .ingest.pipeline import _commit_tag_path
                _commit_tag_path(s, job, Path(job.source_path), tags, score=job.score or 1.0)
                applied += 1
            except Exception:
                pass
    return _toast_response("/review", f"Applied {applied} job(s).")


@app.post("/review/{job_id}/apply")
async def review_apply(
    job_id: int,
    request: Request,
    _: None = Depends(require_auth),
    recording_id: str = Form(...),
    release_id: str = Form(...),
    release_type_override: str | None = Form(None),
    cover_art_url: str = Form(default=""),
    cover_art_file: UploadFile = File(default=None),
):
    """Apply a user-chosen MB candidate to a job stuck in review.

    Re-uses the pipeline's ``_commit_tag_path`` so the cover-art fetch /
    write / move flow is identical to the auto-apply path. Importing it
    inside the function avoids an import cycle at module load time.

    If the user selected a cover from the thumbnail strip (``cover_art_url``)
    or uploaded a custom image (``cover_art_file``), those bytes are set on
    the tags object before calling ``_commit_tag_path`` — which skips its own
    CAA fetch when ``tags.cover_bytes`` is already populated.
    """
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        try:
            tags = mbq.assemble_tags(release_id=release_id, recording_id=recording_id)
        except Exception as e:
            raise HTTPException(500, str(e))
        if release_type_override:
            tags.release_type = release_type_override

        # Cover art override: custom upload takes priority over URL selection.
        if cover_art_file and cover_art_file.filename:
            tags.cover_bytes = await cover_art_file.read()
            tags.cover_mime = cover_art_file.content_type or "image/jpeg"
        elif cover_art_url:
            import requests as _req
            try:
                r = _req.get(cover_art_url, timeout=10, allow_redirects=True)
                r.raise_for_status()
                tags.cover_bytes = r.content
                tags.cover_mime = r.headers.get("content-type", "image/jpeg")
            except Exception:
                pass  # fall back to normal CAA fetch inside _commit_tag_path

        from .ingest.pipeline import _commit_tag_path
        _commit_tag_path(s, job, Path(job.source_path), tags, score=job.score or 1.0)
    return RedirectResponse("/review", status_code=303)


@app.post("/review/{job_id}/resolve_conflict")
def resolve_conflict(
    job_id: int,
    request: Request,
    _: None = Depends(require_auth),
    action: str = Form(...),  # "replace" | "rename" | "skip"
):
    """Handle a destination-exists conflict per user choice."""
    with session() as s:
        job = s.get(Job, job_id)
        if not job or not job.destination_path:
            raise HTTPException(400, "no destination recorded")
        src = Path(job.source_path)
        dest = Path(job.destination_path)

        if action == "skip":
            job.status = JobStatus.skipped
            s.add(job)
            s.commit()
            return RedirectResponse("/review", status_code=303)

        if action == "replace":
            res = move(src, dest, overwrite=True)
        else:  # "rename" — append "-1", "-2", … until a free slot is found
            dest = unique_path(dest)
            res = move(src, dest, overwrite=False)

        if res.moved:
            job.status = JobStatus.done
            job.destination_path = str(dest)
        s.add(job)
        s.commit()
    return RedirectResponse("/review", status_code=303)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: None = Depends(require_auth), saved: str = ""):
    return templates.TemplateResponse(
        request, "settings.html", {
            "request": request,
            "settings": settings(),
            "active_page": "settings",
            "saved": bool(saved),
        }
    )


@app.post("/settings")
def settings_update(
    request: Request,
    _: None = Depends(require_auth),
    acoustid_enabled: str | None = Form(None),
    lyrics_enabled: str | None = Form(None),
    dry_run: str | None = Form(None),
    format_title_case: str | None = Form(None),
    format_fix_qualifiers: str | None = Form(None),
    format_grammar_correct: str | None = Form(None),
    format_grammar_fix_allcaps: str | None = Form(None),
    format_grammar_fix_contractions: str | None = Form(None),
    format_grammar_fix_possessives: str | None = Form(None),
    format_grammar_fix_punct_spacing: str | None = Form(None),
    score_threshold: float = Form(...),
    filename_template_single: str = Form(...),
    filename_template_multidisc: str = Form(...),
    multidisc_folder_template: str = Form(...),
    watcher_enabled: str | None = Form(None),
    genre_limit: int = Form(3),
    genre_casing: str = Form("title"),
    skip_fields: list[str] = Form(default=[]),
    sep_ARTIST: str = Form(...),
    sep_album_artist: str = Form(...),
    sep_ARTISTS: str = Form(...),
    sep_ARTISTSORT: str = Form(...),
    sep_ALBUMARTISTSORT: str = Form(...),
    sep_GENRE: str = Form(...),
    sep_LABEL: str = Form(...),
    sep_ISRC: str = Form(...),
    sep_COMPOSER: str = Form(...),
    sep_CONDUCTOR: str = Form(...),
    sep_LYRICIST: str = Form(...),
    sep_ARRANGER: str = Form(...),
    webhook_url: str = Form(""),
    webhook_on_done: str | None = Form(None),
    webhook_on_error: str | None = Form(None),
):
    """Persist a settings patch from the UI form.

    Unchecked checkboxes don't send a value at all, so ``acoustid_enabled``
    and ``watcher_enabled`` arrive as ``None`` when off (hence the ``bool(...)``
    coercion). Separators are merged on top of the existing dict so untouched
    keys keep their defaults.
    """
    patch = {
        "acoustid_enabled": bool(acoustid_enabled),
        "lyrics_enabled": bool(lyrics_enabled),
        "dry_run": bool(dry_run),
        "format_title_case": bool(format_title_case),
        "format_fix_qualifiers": bool(format_fix_qualifiers),
        "format_grammar_correct": bool(format_grammar_correct),
        "format_grammar_fix_allcaps": bool(format_grammar_fix_allcaps),
        "format_grammar_fix_contractions": bool(format_grammar_fix_contractions),
        "format_grammar_fix_possessives": bool(format_grammar_fix_possessives),
        "format_grammar_fix_punct_spacing": bool(format_grammar_fix_punct_spacing),
        "score_threshold": score_threshold,
        "filename_template_single": filename_template_single,
        "filename_template_multidisc": filename_template_multidisc,
        "multidisc_folder_template": multidisc_folder_template,
        "watcher_enabled": bool(watcher_enabled),
        "genre_limit": genre_limit,
        "genre_casing": genre_casing,
        "skip_fields": skip_fields,
        "separators": {
            **settings().separators.model_dump(),
            "ARTIST": sep_ARTIST,
            "album_artist": sep_album_artist,
            "ARTISTS": sep_ARTISTS,
            "ARTISTSORT": sep_ARTISTSORT,
            "ALBUMARTISTSORT": sep_ALBUMARTISTSORT,
            "GENRE": sep_GENRE,
            "LABEL": sep_LABEL,
            "ISRC": sep_ISRC,
            "COMPOSER": sep_COMPOSER,
            "CONDUCTOR": sep_CONDUCTOR,
            "LYRICIST": sep_LYRICIST,
            "ARRANGER": sep_ARRANGER,
        },
        "webhook_url": webhook_url,
        "webhook_on_done": bool(webhook_on_done),
        "webhook_on_error": bool(webhook_on_error),
    }
    store().update(patch)
    back = request.headers.get("referer", "/settings")
    sep = "&" if "?" in back else "?"
    resp = RedirectResponse(f"{back}{sep}saved=1", status_code=303)
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"message": "Settings saved.", "level": "success"}})
    return resp


@app.post("/settings/test-webhook")
def settings_test_webhook(request: Request, _: None = Depends(require_auth)):
    """Fire a sample payload to the configured webhook URL."""
    url = settings().webhook_url
    if not url:
        return HTMLResponse(
            '<span class="text-[#ffb4b4]">No webhook URL configured.</span>',
            status_code=200,
        )
    from .notify import post_done
    dummy_job = Job(
        id=0, source_path="test.flac", original_name="test.flac",
        status=JobStatus.done, score=1.0,
    )
    from .tagging.schema import TrackTags
    dummy_tags = TrackTags(title="Test Track", artist_display="dragontag Test", album="Test Album")
    try:
        post_done(dummy_job, dummy_tags)
        return HTMLResponse('<span class="text-[#c7f0c7]">Webhook fired successfully.</span>')
    except Exception as e:
        return HTMLResponse(f'<span class="text-[#ffb4b4]">Webhook error: {e}</span>')


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


_LIBRARY_PAGE_SIZES = (10, 25, 50, 100, 200)
_LIBRARY_SORT_COLS = {
    "title": Track.title,
    "artist": Track.artist,
    "album": Track.album,
    "disc": Track.disc_num,
    "track": Track.track_num,
    "duration": Track.duration,
    "path": Track.path,
}


@app.get("/library", response_class=HTMLResponse)
def library(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: str | None = None,
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    sort: str = "album",
    dir: str = "asc",
):
    fid: int | None = int(folder_id) if folder_id and folder_id.strip().isdigit() else None
    if page_size not in _LIBRARY_PAGE_SIZES:
        page_size = 50
    if sort not in _LIBRARY_SORT_COLS:
        sort = "album"
    if dir not in ("asc", "desc"):
        dir = "asc"
    page = max(1, page)
    with session() as s:
        folders = s.exec(select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)).all()
        active_id = fid or (folders[0].id if folders else None)
        tracks, total = _query_tracks(s, active_id, q, page, page_size, sort, dir)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "request": request,
            "folders": folders,
            "active_id": active_id,
            "tracks": tracks,
            "q": q,
            "page": page,
            "page_size": page_size,
            "page_sizes": _LIBRARY_PAGE_SIZES,
            "total": total,
            "total_pages": total_pages,
            "sort": sort,
            "dir": dir,
            "active_page": "library",
            "settings": settings(),
        },
    )


@app.get("/library/tracks", response_class=HTMLResponse)
def library_tracks(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: str | None = None,
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    sort: str = "album",
    dir: str = "asc",
):
    """HTMX partial: filtered track table."""
    fid: int | None = int(folder_id) if folder_id and folder_id.strip().isdigit() else None
    if page_size not in _LIBRARY_PAGE_SIZES:
        page_size = 50
    if sort not in _LIBRARY_SORT_COLS:
        sort = "album"
    if dir not in ("asc", "desc"):
        dir = "asc"
    page = max(1, page)
    with session() as s:
        tracks, total = _query_tracks(s, fid, q, page, page_size, sort, dir)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(request, "_library_tracks.html", {
        "request": request, "tracks": tracks,
        "page": page, "page_size": page_size, "page_sizes": _LIBRARY_PAGE_SIZES,
        "total": total, "total_pages": total_pages,
        "sort": sort, "dir": dir,
        "folder_id": fid, "q": q,
    })


def _query_tracks(s, folder_id, q, page, page_size, sort, dir):
    stmt = select(Track)
    count_stmt = select(func.count(Track.id))
    if folder_id is not None:
        stmt = stmt.where(Track.library_folder_id == folder_id)
        count_stmt = count_stmt.where(Track.library_folder_id == folder_id)
    if q:
        like = f"%{q}%"
        cond = or_(Track.title.ilike(like), Track.artist.ilike(like), Track.album.ilike(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    col = _LIBRARY_SORT_COLS[sort]
    stmt = stmt.order_by(col.desc() if dir == "desc" else col.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    total = s.exec(count_stmt).one() or 0
    return s.exec(stmt).all(), total


@app.get("/library/folders", response_class=HTMLResponse)
def library_folders(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        folders = s.exec(select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)).all()
    return templates.TemplateResponse(request, "library_folders.html", {"request": request, "folders": folders, "active_page": "library"})


@app.post("/library/folders")
def library_folders_add(
    request: Request,
    _: None = Depends(require_auth),
    path: str = Form(...),
    label: str = Form(""),
    priority: int = Form(0),
):
    with session() as s:
        s.add(LibraryFolder(path=path.strip(), label=label.strip(), priority=priority))
        s.commit()
    return RedirectResponse("/library/folders", status_code=303)


@app.post("/library/folders/{folder_id}/delete")
def library_folders_delete(folder_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        f = s.get(LibraryFolder, folder_id)
        if f:
            s.delete(f)
            s.commit()
    return RedirectResponse("/library/folders", status_code=303)


@app.post("/library/scan")
def library_scan(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    with session() as s:
        f = s.get(LibraryFolder, folder_id)
        if not f:
            raise HTTPException(404)
        folder_path, fid = Path(f.path), f.id

    def _run():
        from .library.scanner import scan_folder
        scan_folder(folder_path, fid)

    threading.Thread(target=_run, daemon=True, name="scanner").start()
    return _toast_response("/library", "Folder scan started.")


@app.post("/library/organize")
def library_organize(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    with session() as s:
        f = s.get(LibraryFolder, folder_id)
        if not f:
            raise HTTPException(404)
        fid = f.id

    def _run():
        from .library.organizer import organize_folder
        organize_folder(fid)

    threading.Thread(target=_run, daemon=True, name="organizer").start()
    return _toast_response("/library", "Folder organization started.")


@app.post("/library/bulk-retag")
def library_bulk_retag(
    request: Request,
    _: None = Depends(require_auth),
    source_path: str = Form(...),
    dry_run: str | None = Form(None),
):
    from .ingest.bulk import enqueue_folder
    # Allow per-request dry_run override; fall back to global setting.
    if dry_run is not None:
        store().update({"dry_run": bool(dry_run)})
    try:
        enqueue_folder(Path(source_path.strip()))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _toast_response("/", "Full library re-tag queued.")


@app.post("/library/retag-selected")
def library_retag_selected(
    request: Request,
    _: None = Depends(require_auth),
    track_ids: list[int] = Form(default=[]),
    dry_run: str | None = Form(None),
    select_all_folder: str = Form(default=""),
):
    """Enqueue specific tracks by their Track.id for re-tagging.

    When ``select_all_folder`` is set the entire folder is enqueued,
    bypassing the per-page checkbox selection.
    """
    if dry_run is not None:
        store().update({"dry_run": bool(dry_run)})
    queued = 0
    with session() as s:
        if select_all_folder and select_all_folder.strip().isdigit():
            fid = int(select_all_folder)
            all_tracks = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
        elif select_all_folder == "":
            all_tracks = []
        else:
            all_tracks = []

        ids_to_process = [t.id for t in all_tracks] if all_tracks else track_ids

        for tid in ids_to_process:
            track = s.get(Track, tid)
            if not track:
                continue
            p = Path(track.path)
            if not p.exists():
                continue
            job = pipeline.enqueue(p)
            pipeline.submit(job.id)
            queued += 1
    return _toast_response("/library", f"Queued {queued} track(s) for re-tagging.")


@app.post("/library/fetch-lyrics")
def library_fetch_lyrics(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Fetch and embed lyrics for all tracks in a folder without re-tagging."""
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    track_paths = [(t.title, t.artist, t.album, Path(t.path)) for t in tracks if Path(t.path).exists()]

    def _run():
        from .tagging import lyrics_fetcher
        from .tagging.advisory import is_explicit
        from .tagging.partial import write_lyrics
        for title, artist, album, p in track_paths:
            try:
                fetched = lyrics_fetcher.fetch(artist=artist, title=title, album=album)
                if fetched:
                    advisory = 1 if is_explicit(fetched) else 0
                    write_lyrics(p, fetched, advisory)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True, name="fetch-lyrics").start()
    return _toast_response("/library", f"Fetching lyrics for {len(track_paths)} tracks.")


@app.post("/library/tag-advisories")
def library_tag_advisories(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Re-evaluate advisory rating from existing embedded lyrics."""
    with session() as s:
        tracks = s.exec(select(Track).where(Track.library_folder_id == folder_id)).all()
    track_paths = [Path(t.path) for t in tracks if Path(t.path).exists()]

    def _run():
        from .tagging.advisory import is_explicit
        from .tagging.partial import read_lyrics, write_advisory
        for p in track_paths:
            try:
                lyrics = read_lyrics(p)
                if not lyrics:
                    continue
                write_advisory(p, 1 if is_explicit(lyrics) else 0)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True, name="tag-advisories").start()
    return _toast_response("/library", f"Tagging advisories for {len(track_paths)} tracks.")


@app.post("/library/fetch-covers")
def library_fetch_covers(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Fetch and embed cover art for tracks that have MB IDs."""
    with session() as s:
        tracks = s.exec(select(Track).where(
            Track.library_folder_id == folder_id,
            Track.mb_album_id.is_not(None),
        )).all()
    track_data = [(Path(t.path), t.mb_album_id) for t in tracks if Path(t.path).exists()]

    def _run():
        from .tagging.coverart import fetch_for_release
        from .tagging.partial import write_cover
        from .library.mover import write_cover_jpg
        for p, mb_album_id in track_data:
            try:
                cover = fetch_for_release(mb_album_id)
                if not cover:
                    continue
                write_cover(p, cover.data, cover.mime)
                write_cover_jpg(
                    p.parent, cover.data,
                    min_overwrite_pixels=settings().cover_min_overwrite_pixels,
                    new_width=cover.width,
                )
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True, name="fetch-covers").start()
    return _toast_response("/library", f"Fetching covers for {len(track_data)} tracks.")


@app.post("/library/extract-covers")
def library_extract_covers(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    def _run():
        from .library.actions import extract_embedded_covers
        extract_embedded_covers(folder_id)
    threading.Thread(target=_run, daemon=True, name="extract-covers").start()
    return _toast_response("/library", "Extracting embedded cover art.")


@app.post("/library/replaygain")
def library_replaygain(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    def _run():
        from .library.actions import recompute_replaygain
        result = recompute_replaygain(folder_id)
        log = logging.getLogger(__name__)
        log.info("replaygain result: %s", result)
    threading.Thread(target=_run, daemon=True, name="replaygain").start()
    return _toast_response("/library", "ReplayGain recompute started (requires rsgain/loudgain).")


@app.post("/library/verify-integrity")
def library_verify_integrity(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    def _run():
        from .library.actions import verify_integrity
        verify_integrity(folder_id)
    threading.Thread(target=_run, daemon=True, name="verify-integrity").start()
    return _toast_response("/library", "Integrity check started — see logs.")


@app.post("/library/fix-disc-folders")
def library_fix_disc_folders(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    def _run():
        from .library.actions import fix_disc_folders
        fix_disc_folders(folder_id)
    threading.Thread(target=_run, daemon=True, name="fix-disc-folders").start()
    return _toast_response("/library", "Disc-folder normalization started.")


@app.post("/library/find-missing-tracks")
def library_find_missing_tracks(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    def _run():
        from .library.actions import find_missing_tracks
        find_missing_tracks(folder_id)
    threading.Thread(target=_run, daemon=True, name="find-missing").start()
    return _toast_response("/library", "Scanning for missing tracks — see logs.")


@app.get("/api/mb-search", response_class=HTMLResponse)
def api_mb_search(request: Request, _: None = Depends(require_auth), q: str = ""):
    """HTMX partial: search MusicBrainz by free-text query from the review page."""
    cands = mbq.search_candidates(title=q, artist=None, album=None, limit=10) if q.strip() else []
    return templates.TemplateResponse(request, "_mb_search_results.html", {
        "request": request,
        "cands": [
            {
                "recording_id": c.recording_id,
                "release_id": c.release_id,
                "score": c.score,
                "title": c.raw_recording.get("title", ""),
                "album": c.raw_release.get("title", ""),
            }
            for c in cands
        ],
        "searched": bool(q.strip()),
    })


@app.get("/docs", response_class=HTMLResponse)
def docs(request: Request, _: None = Depends(require_auth)):
    return templates.TemplateResponse(request, "docs.html", {"request": request, "active_page": "docs"})
