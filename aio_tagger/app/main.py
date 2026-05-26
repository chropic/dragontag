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
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .config import env, settings, store
from .db import session
from .identify import musicbrainz as mbq
from .ingest import pipeline, uploads, watcher
from .library.mover import move
from .models import Job, JobStatus

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="aio-tagger")

# Cookie-signing secret comes from the session-secret Docker secret. The
# middleware itself implements signed but unencrypted cookies — fine for
# storing only the username; never put sensitive data in the session.
app.add_middleware(
    SessionMiddleware,
    secret_key=env().resolve_session_secret(),
    https_only=False,
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


def require_auth(request: Request) -> None:
    """FastAPI dependency: redirect unauthenticated users to /login.

    Raising HTTPException with a 303 + Location header is the FastAPI-idiomatic
    way to do a "stop processing this handler" redirect from a dependency.
    """
    if not auth.is_authenticated(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == env().username and auth.verify(password):
        auth.login(request, username)
        return RedirectResponse("/", status_code=303)
    # Generic error — don't leak whether the username or password was wrong.
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid credentials"},
        status_code=401,
    )


@app.post("/logout")
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard + uploads
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        recent = s.exec(select(Job).order_by(Job.updated_at.desc()).limit(50)).all()
    return templates.TemplateResponse("dashboard.html", {"request": request, "jobs": recent})


@app.get("/jobs/table", response_class=HTMLResponse)
def jobs_table(request: Request, _: None = Depends(require_auth)):
    """HTMX partial: just the table body, polled every 5s by the dashboard."""
    with session() as s:
        recent = s.exec(select(Job).order_by(Job.updated_at.desc()).limit(50)).all()
    return templates.TemplateResponse("_jobs_table.html", {"request": request, "jobs": recent})


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
def review_apply(
    job_id: int,
    request: Request,
    _: None = Depends(require_auth),
    recording_id: str = Form(...),
    release_id: str = Form(...),
    release_type_override: str | None = Form(None),
):
    """Apply a user-chosen MB candidate to a job stuck in review.

    Re-uses the pipeline's ``_commit_tag_path`` so the cover-art fetch /
    write / move flow is identical to the auto-apply path. Importing it
    inside the function avoids an import cycle at module load time.
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
            i = 1
            cand = dest.with_stem(f"{dest.stem}-{i}")
            while cand.exists():
                i += 1
                cand = dest.with_stem(f"{dest.stem}-{i}")
            res = move(src, cand, overwrite=False)
            dest = cand

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
    score_threshold: float = Form(...),
    filename_template_single: str = Form(...),
    filename_template_multidisc: str = Form(...),
    multidisc_folder_template: str = Form(...),
    watcher_enabled: str | None = Form(None),
    sep_ARTIST: str = Form(...),
    sep_album_artist: str = Form(...),
    sep_ARTISTS: str = Form(...),
    sep_GENRE: str = Form(...),
):
    """Persist a settings patch from the UI form.

    Unchecked checkboxes don't send a value at all, so ``acoustid_enabled``
    and ``watcher_enabled`` arrive as ``None`` when off (hence the ``bool(...)``
    coercion). Separators are merged on top of the existing dict so untouched
    keys keep their defaults.
    """
    patch = {
        "acoustid_enabled": bool(acoustid_enabled),
        "score_threshold": score_threshold,
        "filename_template_single": filename_template_single,
        "filename_template_multidisc": filename_template_multidisc,
        "multidisc_folder_template": multidisc_folder_template,
        "watcher_enabled": bool(watcher_enabled),
        "separators": {
            **settings().separators.model_dump(),
            "ARTIST": sep_ARTIST,
            "album_artist": sep_album_artist,
            "ARTISTS": sep_ARTISTS,
            "GENRE": sep_GENRE,
        },
    }
    store().update(patch)
    return RedirectResponse("/settings", status_code=303)
