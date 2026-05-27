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

import logging
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .config import env, settings, store
from .db import session
from .identify import musicbrainz as mbq
from .ingest import pipeline, uploads, watcher
from .library.mover import move
from .library.paths import unique_path
from .models import Job, JobStatus, LibraryFolder, ReviewReason, Track

logging.basicConfig(level=logging.INFO)

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
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))


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
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if env().resolve_password() is None:
        return RedirectResponse("/setup", status_code=303)
    if username == env().username and auth.verify(password):
        auth.login(request, username)
        return RedirectResponse("/", status_code=303)
    # Generic error — don't leak whether the username or password was wrong.
    return templates.TemplateResponse(
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
    return templates.TemplateResponse("setup.html", {"request": request, "error": None, "username": env().username})


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
        jobs = s.exec(
            select(Job).order_by(Job.updated_at.desc())
            .offset((page - 1) * _PER_PAGE).limit(_PER_PAGE)
        ).all()
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "jobs": jobs, "page": page, "total_pages": total_pages,
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
    return templates.TemplateResponse("_jobs_table.html", {
        "request": request, "jobs": jobs, "page": page, "total_pages": total_pages,
    })


@app.post("/upload")
async def upload(request: Request, _: None = Depends(require_auth), files: list[UploadFile] = []):
    await uploads.save_uploads(files)
    return RedirectResponse("/", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
    return templates.TemplateResponse("job_detail.html", {"request": request, "job": job})


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
    return templates.TemplateResponse("review.html", {"request": request, "items": items})


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
def settings_page(request: Request, _: None = Depends(require_auth)):
    return templates.TemplateResponse(
        "settings.html", {"request": request, "settings": settings()}
    )


@app.post("/settings")
def settings_update(
    request: Request,
    _: None = Depends(require_auth),
    acoustid_enabled: str | None = Form(None),
    lyrics_enabled: str | None = Form(None),
    dry_run: str | None = Form(None),
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
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, _: None = Depends(require_auth), folder_id: int | None = None, q: str = ""):
    with session() as s:
        folders = s.exec(select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)).all()
        active_id = folder_id or (folders[0].id if folders else None)
        tracks = _query_tracks(s, active_id, q)
    return templates.TemplateResponse(
        "library.html",
        {"request": request, "folders": folders, "active_id": active_id, "tracks": tracks, "q": q},
    )


@app.get("/library/tracks", response_class=HTMLResponse)
def library_tracks(request: Request, _: None = Depends(require_auth), folder_id: int | None = None, q: str = ""):
    """HTMX partial: filtered track table."""
    with session() as s:
        tracks = _query_tracks(s, folder_id, q)
    return templates.TemplateResponse("_library_tracks.html", {"request": request, "tracks": tracks})


def _query_tracks(s, folder_id: int | None, q: str) -> list:
    stmt = select(Track)
    if folder_id is not None:
        stmt = stmt.where(Track.library_folder_id == folder_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Track.title.ilike(like), Track.artist.ilike(like), Track.album.ilike(like))
        )
    stmt = stmt.order_by(Track.album, Track.disc_num, Track.track_num, Track.title)
    return s.exec(stmt).all()


@app.get("/library/folders", response_class=HTMLResponse)
def library_folders(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        folders = s.exec(select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)).all()
    return templates.TemplateResponse("library_folders.html", {"request": request, "folders": folders})


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
    return RedirectResponse("/library", status_code=303)


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
    return RedirectResponse("/library", status_code=303)


@app.post("/library/bulk-retag")
def library_bulk_retag(
    request: Request,
    _: None = Depends(require_auth),
    source_path: str = Form(...),
):
    from .ingest.bulk import enqueue_folder
    try:
        enqueue_folder(Path(source_path.strip()))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse("/", status_code=303)
