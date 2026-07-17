"""FastAPI entry point.

Wires up the session middleware, mounts the templates/static dirs, kicks off
the worker thread + watcher on startup, and defines the route handlers
backing the HTMX-driven UI.

Routes are grouped:

* ``/login`` / ``/logout``           — argon2-backed session auth
* ``/`` and ``/jobs/{id}``           — dashboard and per-job detail
* ``/upload``                        — multipart file upload, kicks pipeline
* ``/queue`` and ``/review/...``     — unified queue page (review candidate
                                       picker, conflict resolver, job list);
                                       bare /review and /jobs redirect here
* ``/settings``                      — UI-editable runtime settings
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from . import auth, logsetup, scheduler, tasks
from . import __version__
from .config import env, settings, store
from .db import dashboard_stats, session
from .identify import musicbrainz as mbq
from .ingest import pipeline, uploads, watcher
from .library.mover import move
from .library.paths import unique_path
from .timeutil import now_utc
from .models import (
    ACTIVE_JOB_STATUSES as _ACTIVE_JOB_STATUSES,
    FileChange,
    Job,
    JobStatus,
    LibraryFolder,
    ReviewReason,
    ScheduledTask,
    Track,
)


def _local_tz() -> ZoneInfo:
    """Resolve the display timezone: Docker ``TZ`` env (locked, always wins),
    else the in-app ``settings().timezone`` override, else UTC."""
    tz = os.environ.get("TZ") or settings().timezone or "UTC"
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


async def _read_upload_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile fully, raising once it exceeds *max_bytes*.

    Chunked so we never buffer more than ``max_bytes`` + one chunk in memory.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1 << 20):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, "uploaded file is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _toast_response(
    redirect_url: str, message: str, level: str = "success", job_id: int | None = None
) -> Response:
    """Return a redirect that also carries an HX-Trigger showToast header.

    ``job_id``, when given, makes the toast clickable to that job's detail page.

    Most callers are plain ``<form method=post>`` submits, where the browser
    follows the 303 itself and the HX-Trigger header is never seen by htmx —
    so the toast is *also* encoded into ``dt_toast``/``dt_level``/``dt_job``
    query params, which the toastManager in base.html shows on page load and
    then strips from the URL.
    """
    params: dict[str, str] = {"dt_toast": message, "dt_level": level}
    if job_id is not None:
        params["dt_job"] = str(job_id)
    sep = "&" if "?" in redirect_url else "?"
    resp = RedirectResponse(redirect_url + sep + urlencode(params), status_code=303)
    payload: dict[str, Any] = {"message": message, "level": level}
    if job_id is not None:
        payload["job_id"] = job_id
    resp.headers["HX-Trigger"] = json.dumps({"showToast": payload})
    return resp


def _toast(message: str, level: str = "success") -> Response:
    """Return an empty 204 carrying only a showToast trigger.

    For htmx form posts with ``hx-swap="none"``: shows an on-page toast without
    navigating, so user/validation errors surface as an alert instead of a raw
    JSON error page.
    """
    return Response(
        status_code=204,
        headers={"HX-Trigger": json.dumps({"showToast": {"message": message, "level": level}})},
    )

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# docs_url/redoc_url/openapi_url disabled so FastAPI's built-in Swagger UI does
# not shadow our custom user-manual route at GET /docs (see docs() below).
# Auth-guarded equivalents are served at /api-docs and /openapi.json instead.
app = FastAPI(title="dragontag", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

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
templates.env.globals["describe_cron"] = scheduler.describe_cron


@app.on_event("startup")
def _startup() -> None:
    """Initialize config + DB, start worker, resume in-flight jobs, start watcher."""
    log.info(r"""
 ____    ____    ______  ____    _____   __  __  ______  ______  ____
/\  _`\ /\  _`\ /\  _  \/\  _`\ /\  __`\/\ \/\ \/\__  _\/\  _  \/\  _`\
\ \ \/\ \ \ \L\ \ \ \L\ \ \ \L\_\ \ \/\ \ \ `\\ \/_/\ \/\ \ \L\ \ \ \L\_\
 \ \ \ \ \ \ ,  /\ \  __ \ \ \L_L\ \ \ \ \ \ , ` \ \ \ \ \ \  __ \ \ \L_L
  \ \ \_\ \ \ \\ \\ \ \/\ \ \ \/, \ \ \_\ \ \ \`\ \ \ \ \ \ \ \/\ \ \ \/, \
   \ \____/\ \_\ \_\ \_\ \_\ \____/\ \_____\ \_\ \_\ \ \_\ \ \_\ \_\ \____/
    \/___/  \/_/\/ /\/_/\/_/\/___/  \/_____/\/_/\/_/  \/_/  \/_/\/_/\/___/
                                                        starting up...""")
    store()                       # ensure /config and SQLite are ready
    logsetup.apply(settings().log_verbosity)
    from .tagging.writers._atomic import cleanup_orphaned_temp_files
    removed = cleanup_orphaned_temp_files(env().library_path)
    if removed:
        log.warning("swept %d orphaned .dgtag-* temp file(s) from library", removed)
    pipeline.start_worker()
    pipeline.resubmit_pending()   # finish anything that was mid-flight at last shutdown
    if settings().watcher_enabled:
        watcher.start()
    scheduler.start()


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


@app.get("/openapi.json", include_in_schema=False)
def openapi_json(request: Request, _: None = Depends(require_auth)):
    """Auth-guarded OpenAPI schema (the unauthenticated default is disabled)."""
    return JSONResponse(app.openapi())


@app.get("/api-docs", include_in_schema=False)
def api_docs(request: Request, _: None = Depends(require_auth)):
    """Swagger UI, served separately so the user manual keeps GET /docs."""
    return get_swagger_ui_html(openapi_url="/openapi.json", title="dragontag API")


@app.get("/api/progress")
def api_progress(request: Request, _: None = Depends(require_auth)):
    """Lightweight poll target for the universal top-of-page progress bar."""
    with session() as s:
        active = s.exec(
            select(Job)
            .where(Job.status.in_(list(_ACTIVE_JOB_STATUSES)), Job.status != JobStatus.queued)
            .order_by(Job.updated_at.desc())
        ).first()
        queued = s.exec(
            select(func.count(Job.id)).where(Job.status == JobStatus.queued)
        ).one() or 0
        if active is None and queued:
            active = s.exec(
                select(Job).where(Job.status == JobStatus.queued).order_by(Job.updated_at.desc())
            ).first()

    if active is None:
        return JSONResponse({"active": False, "label": "", "percent": None, "queued": 0})

    percent = None
    current = active.progress_current
    total = active.progress_total
    label = active.original_name or active.kind
    if total:
        percent = round(100 * (current or 0) / total)
    else:
        label = f"{label} — {active.status.value}"
    return JSONResponse({
        "active": True,
        "label": label,
        "percent": percent,
        "current": current,
        "total": total,
        "item": active.progress_item,
        "queued": queued,
        # Running background tasks (scan/organize/…) can be stopped via
        # POST /jobs/{id}/cancel; pipeline ingest jobs cannot.
        "job_id": active.id,
        "stoppable": active.status == JobStatus.running,
    })


@app.get("/api/cron-describe")
def api_cron_describe(request: Request, _: None = Depends(require_auth), expr: str = ""):
    """Live helper for the Schedule form: cron expression → human description."""
    desc = scheduler.describe_cron(expr.strip())
    return JSONResponse({"valid": desc is not None, "description": desc or ""})


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
        {"request": request, "error": "Invalid credentials", "username": username},
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
    job_ids, errors = await uploads.save_uploads(files)
    n = len(job_ids)
    if not n and not errors:
        return _toast("No files selected.", "error")
    msg = f"Queued {n} file(s)." if n else ""
    if errors:
        msg = (msg + " " if msg else "") + f"Rejected {len(errors)}: {errors[0]}" + (" …" if len(errors) > 1 else "")
    return _toast(msg, "error" if errors and not n else "success")


_QUEUE_PREFS_COOKIE = "queue_prefs"


@app.get("/queue", response_class=HTMLResponse)
def queue_page(
    request: Request,
    _: None = Depends(require_auth),
    page: int = 1,
    page_size: int = 50,
):
    """Unified queue page: review items on top, the full job list below."""
    page = max(1, page)
    # Remember the jobs-list page size across visits; only fall back to the
    # cookie when the URL didn't pin it explicitly.
    if "page_size" not in request.query_params:
        try:
            page_size = int(_prefs_cookie(request, _QUEUE_PREFS_COOKIE).get("page_size", page_size))
        except (TypeError, ValueError):
            pass
    if page_size not in _LIBRARY_PAGE_SIZES:
        page_size = 50
    with session() as s:
        items = s.exec(
            select(Job)
            .where(Job.status == JobStatus.needs_review)
            .order_by(Job.updated_at.desc())
        ).all()
        total = s.exec(select(func.count(Job.id))).one()
        jobs = s.exec(
            select(Job).order_by(Job.updated_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        pending = s.exec(select(func.count(Job.id)).where(
            Job.status.in_(list(_ACTIVE_JOB_STATUSES))
        )).one()
        done_today = s.exec(select(func.count(Job.id)).where(Job.status == JobStatus.done)).one()
        errors = s.exec(select(func.count(Job.id)).where(Job.status == JobStatus.error)).one()
    total_pages = max(1, (total + page_size - 1) // page_size)
    response = templates.TemplateResponse(request, "queue.html", {
        "request": request,
        "items": items,
        "jobs": jobs,
        "page": page,
        "page_size": page_size,
        "page_sizes": _LIBRARY_PAGE_SIZES,
        "total_pages": total_pages,
        "pending": pending,
        "done_today": done_today,
        "errors": errors,
        "active_page": "queue",
    })
    response.set_cookie(
        _QUEUE_PREFS_COOKIE,
        json.dumps({"page_size": page_size}),
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
    )
    return response


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, page: int = 1):
    """Legacy URL — the Jobs page merged into /queue."""
    suffix = f"?page={page}" if page > 1 else ""
    return RedirectResponse(f"/queue{suffix}", status_code=308)


@app.post("/jobs/clear-completed")
def jobs_clear_completed(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.done)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/queue", f"Cleared {len(rows)} completed job(s).")


@app.post("/jobs/clear-errors")
def jobs_clear_errors(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.error)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/queue", f"Cleared {len(rows)} error job(s).")


@app.post("/jobs/clear-all")
def jobs_clear_all(request: Request, _: None = Depends(require_auth)):
    """Delete every Job row that is not currently in-flight."""
    active = set(_ACTIVE_JOB_STATUSES)
    with session() as s:
        rows = s.exec(select(Job).where(~Job.status.in_(active))).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/queue", f"Cleared {len(rows)} job(s).")


@app.post("/jobs/clear-selected")
def jobs_clear_selected(
    request: Request,
    _: None = Depends(require_auth),
    job_ids: list[int] = Form(default=[]),
):
    """Delete specific Job rows chosen via the per-row checkboxes.

    In-flight jobs are skipped (same guard as clear-all) so a running pipeline
    isn't yanked out from under itself. DB rows only — files are never touched.
    """
    active = set(_ACTIVE_JOB_STATUSES)
    deleted = 0
    with session() as s:
        for jid in job_ids:
            r = s.get(Job, jid)
            if r and r.status not in active:
                s.delete(r)
                deleted += 1
        s.commit()
    return _toast_response("/queue", f"Cleared {deleted} job(s).")


@app.post("/jobs/clear-needs-review")
def jobs_clear_needs_review(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.needs_review)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/queue", f"Cleared {len(rows)} needs-review job(s).")


@app.post("/jobs/cancel-queued")
def jobs_cancel_queued(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(Job).where(Job.status == JobStatus.queued)).all()
        for r in rows:
            r.status = JobStatus.skipped
            s.add(r)
        s.commit()
    return _toast_response("/queue", f"Cancelled {len(rows)} queued job(s).")


@app.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        if job.status == JobStatus.running:
            # Running background task (scan, organize, …): signal it to stop;
            # the task thread marks the Job skipped at its next check.
            if not tasks.request_cancel(job_id):
                raise HTTPException(400, "task is no longer running")
            return _toast_response("/queue", "Stop requested — the task will halt shortly.")
        if job.status != JobStatus.queued:
            raise HTTPException(400, "only queued jobs and running tasks can be cancelled")
        job.status = JobStatus.skipped
        s.add(job)
        s.commit()
    return RedirectResponse("/queue", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
    return templates.TemplateResponse(request, "job_detail.html", {"request": request, "job": job, "active_page": "queue"})


@app.post("/jobs/{job_id}/requeue")
def job_requeue(job_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        if job.status not in (JobStatus.done, JobStatus.error, JobStatus.skipped):
            raise HTTPException(400, "only done/error/skipped jobs can be requeued")
        if job.kind != "ingest":
            raise HTTPException(400, "background tasks cannot be requeued; re-run them from their page")
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
    # html.escape: this response is built outside Jinja, so nothing else
    # escapes it — log lines embed MB-sourced metadata and tracebacks, which
    # must never be interpreted as markup.
    import html as _html
    text = _html.escape(job.log or job.error or "(no log)")
    return HTMLResponse(
        f'<pre class="text-xs text-[#8a8a8a] whitespace-pre-wrap p-2 m-0">{text}</pre>'
    )


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


@app.get("/review", response_class=HTMLResponse)
def review(request: Request):
    """Legacy URL — the Review page merged into /queue."""
    return RedirectResponse("/queue", status_code=308)


def _apply_review_match(job_id: int, rec: str, rel: str, cover_url: str):
    """Build a background step that applies one chosen review match.

    Mirrors the single-apply path (``review_apply``): assemble tags for the
    chosen recording/release, optionally swap in a user-selected cover, then
    run the shared commit pipeline. Skips silently if the job already left the
    review state (e.g. applied by a concurrent action)."""
    def step(ctx) -> None:
        with session() as s:
            job = s.get(Job, job_id)
            if not job or job.status != JobStatus.needs_review:
                return
            tags = mbq.assemble_tags(release_id=rel, recording_id=rec)
            # Same schema guarantees as the auto-apply path (formatting,
            # RELEASETYPE inference, RELEASESTATUS default) — calling
            # _commit_tag_path directly would otherwise skip them.
            from .ingest.pipeline import prepare_tags
            prepare_tags(job, tags)
            if cover_url:
                from .net import fetch_bytes
                try:
                    r, body = fetch_bytes(
                        cover_url, timeout=10, max_bytes=32 * 1024 * 1024,
                        validate=True, allow_redirects=False,
                    )
                    r.raise_for_status()
                    tags.cover_bytes = body
                    tags.cover_mime = r.headers.get("content-type", "image/jpeg")
                except Exception:
                    pass  # fall back to the normal CAA fetch in _commit_tag_path
            from .ingest.pipeline import _commit_tag_path
            _commit_tag_path(s, job, Path(job.source_path), tags, score=job.score or 1.0)
        ctx.log(f"applied job {job_id}")
    return step


@app.post("/review/bulk-apply")
async def review_bulk_apply(request: Request, _: None = Depends(require_auth)):
    """Apply the user's chosen candidate for each selected review job, as one
    background job so the page returns immediately and the slow (MB-rate-
    limited) work doesn't block the request.

    Per job the chosen recording/release comes from a ``pick_{id}`` field
    ("recording|release", from the candidate radio or a manual MB search),
    falling back to the job's stored top candidate when the user left it
    untouched. An optional ``cover_{id}`` carries the per-job cover selection.
    """
    form = await request.form()
    job_ids = [int(v) for v in form.getlist("job_ids") if str(v).strip().isdigit()]

    steps: list[tuple[str, Any]] = []
    with session() as s:
        for job_id in job_ids:
            job = s.get(Job, job_id)
            if not job or job.status != JobStatus.needs_review:
                continue
            rec = rel = ""
            pick = str(form.get(f"pick_{job_id}", "")).strip()
            if "|" in pick:
                p_rec, _, p_rel = pick.partition("|")
                rec, rel = p_rec.strip(), p_rel.strip()
            if not rec or not rel:
                candidates = (job.candidates_json or {}).get("items", [])
                if not candidates:
                    # No user pick and nothing stored to fall back on (e.g. a
                    # dry-run/conflict item mixed into the batch). Skip it rather
                    # than error the whole apply, and log why so a "fewer applied
                    # than selected" outcome is explainable.
                    log.info(
                        "bulk-apply: skipping job %s (%s) — no pick and no stored candidate",
                        job_id,
                        job.review_reason.value if job.review_reason else "review",
                    )
                    continue
                rec, rel = candidates[0]["recording_id"], candidates[0]["release_id"]
            cover_url = str(form.get(f"cover_{job_id}", "")).strip()
            steps.append((f"Apply job {job_id}", _apply_review_match(job_id, rec, rel, cover_url)))

    if not steps:
        return _toast_response("/queue", "Nothing to apply — select review items first.", "error")
    n = len(steps)
    new_job_id = tasks.run_chain("review_bulk", f"Apply {n} review match(es)", steps)
    return _toast_response(
        "/queue", f"Applying {n} match(es) in the background…", job_id=new_job_id
    )


@app.post("/review/{job_id}/apply")
async def review_apply(
    job_id: int,
    request: Request,
    _: None = Depends(require_auth),
    recording_id: str = Form(default=""),
    release_id: str = Form(default=""),
    pick: str = Form(default=""),
    manual_recording_id: str = Form(default=""),
    manual_release_id: str = Form(default=""),
    release_type_override: str | None = Form(None),
    cover_art_url: str = Form(default=""),
    cover_art_file: UploadFile = File(default=None),
):
    """Apply a user-chosen MB candidate to a job stuck in review.

    Re-uses the pipeline's ``_commit_tag_path`` so the cover-art fetch /
    write / move flow is identical to the auto-apply path. Importing it
    inside the function avoids an import cycle at module load time.

    The MB ids are resolved server-side from whichever control the user used so
    the request can't 422 even if the client-side JS didn't populate the hidden
    fields: explicit ``recording_id``/``release_id`` (JS-populated) → a selected
    radio submitted as ``pick`` ("recording|release", from the candidate list OR
    a manual MB search) → the manual id-entry inputs. If none resolve we bounce
    back to /review with a toast instead of raising.

    If the user selected a cover from the thumbnail strip (``cover_art_url``)
    or uploaded a custom image (``cover_art_file``), those bytes are set on
    the tags object before calling ``_commit_tag_path`` — which skips its own
    CAA fetch when ``tags.cover_bytes`` is already populated.
    """
    rec = recording_id.strip()
    rel = release_id.strip()
    if (not rec or not rel) and "|" in pick:
        p_rec, _, p_rel = pick.partition("|")
        rec = rec or p_rec.strip()
        rel = rel or p_rel.strip()
    rec = rec or manual_recording_id.strip()
    rel = rel or manual_release_id.strip()
    if not rec or not rel:
        return _toast_response("/queue", "Pick a candidate or enter MB ids first.", "error")

    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404)
        if job.status != JobStatus.needs_review:
            # A stale form / double-submit on an already-resolved job must not
            # re-run the commit path — the source has moved, so a second run
            # would fail and flip a done job to error.
            return _toast_response(
                "/queue", f"Job {job_id} is not awaiting review.", "error"
            )
        try:
            tags = mbq.assemble_tags(release_id=rel, recording_id=rec)
        except Exception as e:
            raise HTTPException(500, str(e))
        if release_type_override:
            tags.release_type = release_type_override
        # Shared schema guarantees (formatting, RELEASETYPE inference,
        # RELEASESTATUS default). Runs after the override so an explicit user
        # choice wins; prepare_tags only fills release_type when it's empty.
        from .ingest.pipeline import prepare_tags
        prepare_tags(job, tags)

        # Cover art override: custom upload takes priority over URL selection.
        if cover_art_file and cover_art_file.filename:
            # Cap the in-memory read so an oversized upload can't OOM the worker.
            tags.cover_bytes = await _read_upload_capped(
                cover_art_file, 32 * 1024 * 1024
            )
            tags.cover_mime = cover_art_file.content_type or "image/jpeg"
        elif cover_art_url:
            from .net import fetch_bytes
            try:
                # User-supplied URL → SSRF guard (public host only) + redirects
                # disabled so a 30x can't bounce us onto an internal address,
                # and a size cap so a hostile server can't OOM the worker.
                r, body = fetch_bytes(
                    cover_art_url,
                    timeout=10,
                    max_bytes=32 * 1024 * 1024,
                    validate=True,
                    allow_redirects=False,
                )
                r.raise_for_status()
                tags.cover_bytes = body
                tags.cover_mime = r.headers.get("content-type", "image/jpeg")
            except Exception:
                pass  # fall back to normal CAA fetch inside _commit_tag_path

        from .ingest.pipeline import _commit_tag_path
        _commit_tag_path(s, job, Path(job.source_path), tags, score=job.score or 1.0)
    return RedirectResponse("/queue", status_code=303)


@app.post("/review/{job_id}/resolve_conflict")
def resolve_conflict(
    job_id: int,
    request: Request,
    _: None = Depends(require_auth),
    action: str = Form(...),  # "replace" | "rename" | "skip"
):
    """Handle a destination-exists conflict per user choice."""
    from .library.filelock import path_lock

    with session() as s:
        job = s.get(Job, job_id)
        if not job or not job.destination_path:
            raise HTTPException(400, "no destination recorded")
        if job.status != JobStatus.needs_review:
            # A stale form / double-submit after the conflict was already
            # resolved must not re-run the move — the source has moved on, so
            # a second attempt would 500 or clobber the resolved file.
            return _toast_response(
                "/queue", f"Job {job_id} is not awaiting review.", "error"
            )
        src = Path(job.source_path)
        dest = Path(job.destination_path)

        if action == "skip":
            job.status = JobStatus.skipped
            s.add(job)
            s.commit()
            return RedirectResponse("/queue", status_code=303)

        # This handler mutates a physical file just like the pipeline /
        # organizer / revert — hold the per-path lock so it can't interleave
        # with the worker or a concurrent revert touching the same file.
        with path_lock(src):
            if action == "replace":
                res = move(src, dest, overwrite=True)
            else:  # "rename" — append "-1", "-2", … until a free slot is found
                dest = unique_path(dest)
                res = move(src, dest, overwrite=False)

            if res.moved:
                from .library.mover import move_lyric_sidecar
                move_lyric_sidecar(src, dest)

        if not res.moved:
            # A rename slot raced away / replace refused — the file is still
            # at ``src``. Say so instead of silently leaving the job in review.
            return _toast_response(
                "/queue",
                f"Could not move {src.name}: {dest} is occupied. Try again.",
                "error",
            )

        from .ingest.pipeline import _pick_library_folder
        from .library.scanner import _upsert_from_disk

        job.status = JobStatus.done
        job.destination_path = str(dest)
        # The tags were already written before the conflict was detected —
        # index the moved file now so it shows up in the library without a
        # manual rescan. For "replace" this also refreshes the row that
        # still described the overwritten file's old metadata.
        lib_root = _pick_library_folder()
        folder_row = s.exec(
            select(LibraryFolder).where(LibraryFolder.path == str(lib_root))
        ).first()
        try:
            track = _upsert_from_disk(s, dest, folder_row.id if folder_row else None)
            s.flush()
            job.track_id = track.id
        except Exception:
            log.exception("resolve_conflict: failed to index %s", dest)
        # Re-point this job's conflict-time FileChange audit row (recorded by
        # the pipeline with file_path = the blocked source) at the file's
        # final location so revert / move-back keep working after the move.
        for change in s.exec(
            select(FileChange).where(
                FileChange.job_id == job.id, FileChange.file_path == str(src)
            )
        ).all():
            change.file_path = str(dest)
            s.add(change)
        s.add(job)
        s.commit()
    return RedirectResponse("/queue", status_code=303)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: None = Depends(require_auth), saved: str = ""):
    tz_env = os.environ.get("TZ")
    return templates.TemplateResponse(
        request, "settings.html", {
            "request": request,
            "settings": settings(),
            "active_page": "settings",
            "saved": bool(saved),
            "tz_env_locked": bool(tz_env),
            "tz_current": tz_env or settings().timezone or "UTC",
            "tz_choices": sorted(available_timezones()),
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
    network_timeout_seconds: float = Form(15.0),
    filename_template_single: str = Form(...),
    filename_template_multidisc: str = Form(...),
    multidisc_folder_template: str = Form(...),
    folder_artist_split_separators: str = Form(""),
    fold_edition_suffixes: str | None = Form(None),
    quarantine_path: str = Form(""),
    cover_allow_release_group_fallback: str | None = Form(None),
    replaygain_tool_path: str = Form(""),
    watcher_enabled: str | None = Form(None),
    genre_limit: int = Form(3),
    genre_casing: str = Form("title"),
    genre_whitelist_enabled: str | None = Form(None),
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
    max_recent_changes: int = Form(500),
    log_verbosity: int = Form(3),
    scan_filter_patterns_raw: str = Form(""),
    scan_exclude_dirs_raw: str = Form(""),
    scan_exclude_files_raw: str = Form(""),
    timezone: str = Form(""),
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
        "network_timeout_seconds": network_timeout_seconds,
        "filename_template_single": filename_template_single,
        "filename_template_multidisc": filename_template_multidisc,
        "multidisc_folder_template": multidisc_folder_template,
        "folder_artist_split_separators": folder_artist_split_separators,
        "fold_edition_suffixes": bool(fold_edition_suffixes),
        "quarantine_path": quarantine_path.strip(),
        "cover_allow_release_group_fallback": bool(cover_allow_release_group_fallback),
        "replaygain_tool_path": replaygain_tool_path.strip(),
        "watcher_enabled": bool(watcher_enabled),
        "genre_limit": genre_limit,
        "genre_casing": genre_casing,
        "genre_whitelist_enabled": bool(genre_whitelist_enabled),
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
        "max_recent_changes": max_recent_changes,
        "log_verbosity": log_verbosity,
        "scan_filter_patterns": [
            ln.strip() for ln in scan_filter_patterns_raw.splitlines() if ln.strip()
        ],
        "scan_exclude_dirs": [
            ln.strip() for ln in scan_exclude_dirs_raw.splitlines() if ln.strip()
        ],
        "scan_exclude_files": [
            ln.strip() for ln in scan_exclude_files_raw.splitlines() if ln.strip()
        ],
        # The Docker TZ env var always wins and locks the field in the UI —
        # ignore whatever the (disabled/absent) form field sent in that case
        # rather than persist a value that can never take effect.
        "timezone": "" if os.environ.get("TZ") else timezone,
    }
    try:
        store().update(patch)
    except PydanticValidationError as e:
        # Out-of-range / invalid values (crafted POST or a value the HTML
        # min/max didn't guard) must come back as a toast, not a raw 500.
        first = e.errors()[0] if e.errors() else {}
        field = ".".join(str(x) for x in first.get("loc", ())) or "settings"
        return _toast_response(
            "/settings", f"Settings not saved — invalid {field}: {first.get('msg', e)}", "error"
        )
    logsetup.apply(settings().log_verbosity)
    # Apply the watcher toggle immediately — it used to be read only at
    # startup, leaving the observer silently running (or off) until restart.
    if settings().watcher_enabled:
        watcher.start()
    else:
        watcher.stop()
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


_LIB_PREFS_COOKIE = "lib_prefs"


def _prefs_cookie(request: Request, name: str) -> dict:
    """Read a small JSON view-preferences cookie, tolerating absence/garbage."""
    raw = request.cookies.get(name)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _library_prefs_from_cookie(request: Request) -> dict:
    """Sort/pagination remembered across visits to /library (folder and
    search are intentionally not persisted)."""
    return _prefs_cookie(request, _LIB_PREFS_COOKIE)


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
    from .library.actions import LIBRARY_ACTIONS
    # Only fall back to the saved cookie for params the URL didn't specify —
    # FastAPI fills in the function defaults either way, so we have to check
    # the raw query string to tell "absent" from "explicitly default".
    prefs = _library_prefs_from_cookie(request)
    qp = request.query_params
    if "sort" not in qp and "sort" in prefs:
        sort = prefs["sort"]
    if "dir" not in qp and "dir" in prefs:
        dir = prefs["dir"]
    if "page_size" not in qp and "page_size" in prefs:
        try:
            page_size = int(prefs["page_size"])
        except (TypeError, ValueError):
            pass
    if "page" not in qp and "page" in prefs:
        try:
            page = int(prefs["page"])
        except (TypeError, ValueError):
            pass

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
    response = templates.TemplateResponse(
        request,
        "library.html",
        {
            "request": request,
            "folders": folders,
            "active_id": active_id,
            "library_actions": [(k, v[0], v[1]) for k, v in LIBRARY_ACTIONS.items()],
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
    response.set_cookie(
        _LIB_PREFS_COOKIE,
        json.dumps({"sort": sort, "dir": dir, "page_size": page_size, "page": page}),
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
    )
    return response


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
        folder_path, fid, label = Path(f.path), f.id, (f.label or f.path)

    from .library.scanner import scan_folder
    job_id = tasks.run_task("scan", f"Scan {label}", lambda ctx: scan_folder(folder_path, fid, ctx=ctx))
    return _toast_response("/library", "Folder scan started — track it on the Jobs page.", job_id=job_id)


@app.post("/library/organize")
def library_organize(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    # Same guard as the batches: organize moves files, and two file-moving
    # background tasks racing over one folder is what the guard prevents.
    if msg := _batch_guard():
        return _toast_response("/library", msg, "error")
    with session() as s:
        f = s.get(LibraryFolder, folder_id)
        if not f:
            raise HTTPException(404)
        fid, label = f.id, (f.label or f.path)

    from .library.organizer import organize_folder
    tasks.run_task("organize", f"Organize {label}", lambda ctx: organize_folder(fid, ctx=ctx))
    return _toast_response("/library", "Folder organization started — track it on the Jobs page.")


@app.post("/library/bulk-retag")
def library_bulk_retag(
    request: Request,
    _: None = Depends(require_auth),
    source_path: str = Form(""),
    folder_id: str = Form(""),
    dry_run: str | None = Form(None),
):
    """THE re-tag entry point: every file goes through the one tagging pass
    (identify → tag → move, album-first). Takes either an explicit server
    path (dashboard form) or a library folder id (Library page card).

    The enqueue walk runs as a background task — a large folder means
    thousands of per-file DB inserts, and doing them in the request thread
    hung the browser until the walk finished. Path validation stays
    in-request so a typo still gets an immediate error toast."""
    from .ingest.bulk import enqueue_folder
    # source_path is optional at the API layer so a blank submit surfaces a
    # friendly toast instead of a raw 422 validation page.
    sp = source_path.strip()
    if not sp and folder_id.strip().isdigit():
        with session() as s:
            f = s.get(LibraryFolder, int(folder_id))
            if f:
                sp = f.path
    if not sp:
        return _toast("Enter a folder path first.", "error")
    src = Path(sp)
    if not src.exists() or not src.is_dir():
        return _toast(f"Not a directory: {src}", "error")
    # Per-request dry-run only — the checkbox never mutates the global setting.
    # An unchecked box submits nothing, which means an explicit "not dry run".
    use_dry_run = bool(dry_run)

    def _run(ctx) -> str:
        ids = enqueue_folder(src, dry_run=use_dry_run, ctx=ctx)
        return f"{len(ids)} file(s) enqueued"

    job_id = tasks.run_task("retag", f"Re-tag {sp}", _run)
    msg = "Re-tag queued — files are being enqueued in the background; track progress on the Queue page."
    if request.headers.get("hx-request"):
        # Dashboard form posts via htmx with hx-swap="none": a 303 would be
        # followed by the XHR and its HX-Trigger toast lost — return the
        # no-navigation toast instead.
        return _toast(msg)
    return _toast_response("/library", msg, job_id=job_id)


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
    queued = 0
    with session() as s:
        if select_all_folder.strip().isdigit():
            fid = int(select_all_folder)
            all_tracks = s.exec(select(Track).where(Track.library_folder_id == fid)).all()
            ids_to_process = [t.id for t in all_tracks]
        elif select_all_folder == "all":
            # "Select all in folder" was clicked while viewing all folders.
            ids_to_process = [t.id for t in s.exec(select(Track)).all()]
        else:
            ids_to_process = track_ids

        for tid in ids_to_process:
            track = s.get(Track, tid)
            if not track or track.protected:
                continue
            p = Path(track.path)
            if not p.exists():
                continue
            # Per-request dry-run only — never mutates the global setting.
            # requeue_reviews: this is an explicit re-tag (like bulk/batch),
            # so a track whose previous run is stuck in needs_review must be
            # reset to queued and actually reprocessed — without it the dedup
            # hit is counted as "queued" but silently skipped by process().
            job = pipeline.enqueue(p, dry_run=bool(dry_run), requeue_reviews=True)
            pipeline.submit(job.id)
            queued += 1
    return _toast_response("/library", f"Queued {queued} track(s) for re-tagging.")


@app.post("/library/tracks/{track_id}/delete")
def library_track_delete(track_id: int, request: Request, _: None = Depends(require_auth)):
    """Remove a Track row from the library DB. The file on disk is untouched.

    Useful for "stuck" entries whose file was moved or deleted outside
    dragontag; a re-scan re-adds anything still on disk.
    """
    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
        name = Path(track.path).name
        # Detach any jobs referencing this track so the FK doesn't dangle.
        for j in s.exec(select(Job).where(Job.track_id == track_id)).all():
            j.track_id = None
            s.add(j)
        s.delete(track)
        s.commit()
    back = request.headers.get("referer", "/library")
    return _toast_response(back, f"Removed {name} from the library list (file not touched).")


def _existing_albums() -> list[dict[str, Any]]:
    """Distinct albums already in the library, for the edit-modal album picker.

    Deduped on ``mb_album_id`` when present, else ``(album, album_artist)`` —
    a plain SELECT DISTINCT over every column would surface the same album
    more than once if its tracks disagree on disc/track totals or MB ids.
    """
    with session() as s:
        rows = s.exec(
            select(
                Track.album,
                Track.album_artist,
                Track.mb_album_id,
                Track.mb_release_group_id,
                Track.disc_total,
                Track.track_total,
            )
            .where(Track.album.is_not(None))
            .distinct()
            .order_by(Track.album_artist, Track.album)
        ).all()
    seen: set[Any] = set()
    albums = []
    for r in rows:
        key = r[2] or (r[0], r[1])
        if key in seen:
            continue
        seen.add(key)
        albums.append({
            "album": r[0], "album_artist": r[1], "mb_album_id": r[2],
            "mb_release_group_id": r[3], "disc_total": r[4], "track_total": r[5],
        })
    return albums


@app.get("/library/tracks/{track_id}/edit", response_class=HTMLResponse)
def library_track_edit_modal(track_id: int, request: Request, _: None = Depends(require_auth)):
    """HTMX partial: the per-track edit menu (manual tags, MB/AcoustID pull,
    protect-from-overwrite, LRCLIB lyrics, link-to-album) opened by clicking
    a track title."""
    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
    return templates.TemplateResponse(request, "_track_edit_modal.html", {
        "request": request, "t": track, "albums": _existing_albums(),
    })


@app.post("/library/tracks/{track_id}/edit")
def library_track_edit_save(
    track_id: int,
    request: Request,
    _: None = Depends(require_auth),
    title: str = Form(""),
    artist: str = Form(""),
    album: str = Form(""),
    album_artist: str = Form(""),
    track_num: str = Form(""),
    track_total: str = Form(""),
    disc_num: str = Form(""),
    disc_total: str = Form(""),
):
    """Save a manual tag correction — updates only these fields on disk,
    leaving the rest of the file's tags (genres, dates, MB ids, lyrics)
    untouched, then refreshes the denormalized Track row to match."""
    from .tagging.partial import write_basic_tags

    def _int(v: str) -> int | None:
        return int(v) if v.strip().isdigit() else None

    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
        p = Path(track.path)
        if not p.exists():
            return _toast_response("/library", f"{p.name}: file not found on disk.", "error")
        fields = dict(
            title=title.strip() or None,
            artist=artist.strip() or None,
            album=album.strip() or None,
            album_artist=album_artist.strip() or None,
            track=_int(track_num),
            track_total=_int(track_total),
            disc=_int(disc_num),
            disc_total=_int(disc_total),
        )
        try:
            # clear_blanks: the modal pre-fills every field, so a blank is a
            # deliberate clear — the file tag must go too, or the next scan
            # would resurrect it and silently undo the edit.
            # path_lock: this is an in-place file mutation, serialized against
            # the ingest worker / organizer / revert like every other mutator.
            from .library.filelock import path_lock
            with path_lock(p):
                write_basic_tags(p, **fields, clear_blanks=True)
        except Exception as e:
            return _toast_response("/library", f"{p.name}: tag write failed: {e}", "error")
        track.title = fields["title"]
        track.artist = fields["artist"]
        track.album = fields["album"]
        track.album_artist = fields["album_artist"]
        track.track_num = fields["track"]
        track.track_total = fields["track_total"]
        track.disc_num = fields["disc"]
        track.disc_total = fields["disc_total"]
        s.add(track)
        s.commit()
        folder_id = track.library_folder_id
    return _toast_response(
        f"/library?folder_id={folder_id or ''}", f"Tags updated for {p.name}."
    )


@app.post("/library/tracks/{track_id}/link-album")
def library_track_link_album(
    track_id: int,
    request: Request,
    _: None = Depends(require_auth),
    mb_album_id: str = Form(""),
    album: str = Form(""),
    album_artist: str = Form(""),
):
    """Inherit album-shared fields from an existing library album onto this
    track — for an orphan/mistagged single that actually belongs to an album
    the user already has. Looks up a representative track of the chosen
    album (preferring ``mb_album_id``, else the ``album``/``album_artist``
    pair) and copies its album-level fields onto both the file and the
    denormalized Track row. The track's own title/artist/track number are
    left untouched."""
    from .tagging.partial import write_album_link_tags

    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
        query = select(Track)
        if mb_album_id.strip():
            query = query.where(Track.mb_album_id == mb_album_id.strip())
        elif album.strip():
            query = query.where(Track.album == album.strip(), Track.album_artist == (album_artist.strip() or None))
        else:
            return _toast_response("/library", "Pick an album first.", "error")
        rep = s.exec(query).first()
        if not rep:
            return _toast_response("/library", "Album not found.", "error")
        p = Path(track.path)
        if not p.exists():
            return _toast_response("/library", f"{p.name}: file not found on disk.", "error")
        fields = dict(
            album=rep.album,
            album_artist=rep.album_artist,
            disc_total=rep.disc_total,
            track_total=rep.track_total,
            mb_album_id=rep.mb_album_id,
            mb_release_group_id=rep.mb_release_group_id,
        )
        try:
            from .library.filelock import path_lock
            with path_lock(p):
                write_album_link_tags(p, **fields)
        except Exception as e:
            return _toast_response("/library", f"{p.name}: tag write failed: {e}", "error")
        track.album = fields["album"]
        track.album_artist = fields["album_artist"]
        track.disc_total = fields["disc_total"]
        track.track_total = fields["track_total"]
        track.mb_album_id = fields["mb_album_id"]
        track.mb_release_group_id = fields["mb_release_group_id"]
        s.add(track)
        s.commit()
        folder_id = track.library_folder_id
    return _toast_response(f"/library?folder_id={folder_id or ''}", f"Linked {p.name} to album.")


@app.post("/library/tracks/{track_id}/protect")
def library_track_protect_toggle(track_id: int, request: Request, _: None = Depends(require_auth)):
    """Toggle overwrite protection for one track.

    Sets ``Track.protected`` (so library batch actions in actions.py skip it)
    and mirrors the path into ``scan_exclude_files`` (so the scanner, watcher
    and bulk re-tag enqueue — which already honor that list — skip it too).
    """
    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
        track.protected = not track.protected
        s.add(track)
        s.commit()
        now_protected = track.protected
        path_str = track.path
        folder_id = track.library_folder_id

    if now_protected:
        def _add(cur):
            excluded = list(cur.scan_exclude_files)
            if path_str not in excluded:
                excluded.append(path_str)
            return {"scan_exclude_files": excluded[-500:]}
        store().transact(_add)
    else:
        def _remove(cur):
            excluded = list(cur.scan_exclude_files)
            if path_str in excluded:
                excluded.remove(path_str)
            return {"scan_exclude_files": excluded}
        store().transact(_remove)

    msg = "Protected from overwrite." if now_protected else "Protection removed."
    return _toast_response(f"/library?folder_id={folder_id or ''}", msg)


@app.post("/library/tracks/{track_id}/identify", response_class=HTMLResponse)
def library_track_identify(track_id: int, request: Request, _: None = Depends(require_auth)):
    """AcoustID fingerprint lookup (falling back to a plain MB text search on
    the track's current tags) — renders the same candidate-picker list used
    by the manual search box, into the track-edit modal."""
    from .identify.relookup import candidates_for_file

    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
    p = Path(track.path)
    cands = []
    if p.exists():
        cands, _ = candidates_for_file(
            p, title=track.title, artist=track.artist, album=track.album, limit=10
        )
    return templates.TemplateResponse(request, "_track_mb_results.html", {
        "request": request, "track_id": track_id, "searched": True,
        "cands": [
            {
                "recording_id": c.recording_id, "release_id": c.release_id, "score": c.score,
                "title": c.raw_recording.get("title", ""),
                "artist": c.raw_recording.get("artist-credit-phrase", ""),
                "album": c.raw_release.get("title", ""),
            } for c in cands
        ],
    })


@app.get("/library/tracks/{track_id}/mb-search", response_class=HTMLResponse)
def library_track_mb_search(
    track_id: int,
    request: Request,
    _: None = Depends(require_auth),
    title: str = "",
    artist: str = "",
    album: str = "",
    mbid: str = "",
):
    """HTMX partial: manual MusicBrainz search from the track-edit modal."""
    search_error = None
    try:
        if mbid.strip():
            cands = mbq.candidates_from_mbid(mbid.strip(), title_hint=title or None)
            searched = True
        else:
            cands = mbq.search_candidates(title=title, artist=artist or None, album=album or None, limit=10, raise_on_error=True) if title.strip() else []
            searched = bool(title.strip())
    except Exception as e:
        log.warning("track mb-search failed: %s", e)
        cands, searched = [], True
        search_error = "MusicBrainz search failed — network error. Try again."
    return templates.TemplateResponse(request, "_track_mb_results.html", {
        "request": request, "track_id": track_id, "searched": searched,
        "search_error": search_error,
        "cands": [
            {
                "recording_id": c.recording_id, "release_id": c.release_id, "score": c.score,
                "title": c.raw_recording.get("title", ""),
                "artist": c.raw_recording.get("artist-credit-phrase", ""),
                "album": c.raw_release.get("title", ""),
            } for c in cands
        ],
    })


@app.post("/library/tracks/{track_id}/apply-match")
def library_track_apply_match(
    track_id: int,
    request: Request,
    _: None = Depends(require_auth),
    pick: str = Form(default=""),
    manual_recording_id: str = Form(default=""),
    manual_release_id: str = Form(default=""),
):
    """Write the chosen MusicBrainz recording/release onto this file in place
    (tags + cover art) and refresh its Track row. The file is not moved —
    this is a metadata correction for a file already in the library."""
    rec = rel = ""
    if "|" in pick:
        p_rec, _, p_rel = pick.partition("|")
        rec, rel = p_rec.strip(), p_rel.strip()
    rec = rec or manual_recording_id.strip()
    rel = rel or manual_release_id.strip()
    if not rec or not rel:
        return _toast_response("/library", "Pick a match first.", "error")

    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
        folder_id = track.library_folder_id

    from .library.retag import apply_match
    ok, msg = apply_match(track_id, rec, rel)
    return _toast_response(
        f"/library?folder_id={folder_id or ''}", msg, "success" if ok else "error"
    )


@app.post("/library/tracks/{track_id}/fetch-lyrics")
def library_track_fetch_lyrics(
    track_id: int,
    request: Request,
    _: None = Depends(require_auth),
    next: str = Form(default=""),
):
    """Fetch synced/plain lyrics from LRCLIB for this one track.

    ``next`` optionally overrides the redirect target so pages other than the
    Library (the Completions page's per-row button) return to themselves;
    only local paths are honored.
    """
    from .tagging import lyrics_fetcher
    from .tagging.advisory import is_explicit
    from .tagging.partial import write_lyrics

    with session() as s:
        track = s.get(Track, track_id)
        if not track:
            raise HTTPException(404)
        p = Path(track.path)
        folder_id = track.library_folder_id
        title, artist, album = track.title, track.artist, track.album

    back = next if next.startswith("/") and not next.startswith("//") else ""
    if not p.exists():
        return _toast_response(back or "/library", f"{p.name}: file not found on disk.", "error")
    fetched = lyrics_fetcher.fetch(artist=artist, title=title, album=album)
    if not fetched:
        return _toast_response(back or f"/library?folder_id={folder_id or ''}", "No lyrics found on LRCLIB.", "error")
    advisory = 1 if is_explicit(fetched) else 0
    from .library.filelock import path_lock
    with path_lock(p):
        write_lyrics(p, fetched, advisory)
    with session() as s:
        track = s.get(Track, track_id)
        if track:
            track.has_lyrics = True
            track.advisory = advisory
            s.add(track)
            s.commit()
    return _toast_response(back or f"/library?folder_id={folder_id or ''}", f"Lyrics fetched for {p.name}.")


@app.post("/library/fetch-lyrics")
def library_fetch_lyrics(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Fetch and embed lyrics for all tracks in a folder without re-tagging."""
    from .library.actions import fetch_lyrics_for_folder
    tasks.run_task("fetch_lyrics", f"Fetch lyrics (folder {folder_id})",
                   lambda ctx: fetch_lyrics_for_folder(folder_id, ctx=ctx))
    return _toast_response("/library", "Lyrics fetch started — track it on the Jobs page.")


@app.post("/library/tag-advisories")
def library_tag_advisories(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Re-evaluate advisory rating from existing embedded lyrics."""
    from .library.actions import tag_advisories_for_folder
    tasks.run_task("tag_advisories", f"Tag advisories (folder {folder_id})",
                   lambda ctx: tag_advisories_for_folder(folder_id, ctx=ctx))
    return _toast_response("/library", "Advisory tagging started — track it on the Queue page.")


@app.post("/library/fix-genres")
def library_fix_genres(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Backfill missing genres from MusicBrainz for tracks that have none."""
    from .library.actions import fix_genres_for_folder
    tasks.run_task("fix_genres", f"Fix genres (folder {folder_id})",
                   lambda ctx: fix_genres_for_folder(folder_id, ctx=ctx))
    return _toast_response("/library", "Genre backfill started — track it on the Queue page.")


@app.post("/library/fetch-covers")
def library_fetch_covers(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
):
    """Fetch and embed cover art for tracks that have MB IDs."""
    from .library.actions import fetch_covers_for_folder
    tasks.run_task("fetch_covers", f"Fetch covers (folder {folder_id})",
                   lambda ctx: fetch_covers_for_folder(folder_id, ctx=ctx))
    return _toast_response("/library", "Cover fetch started — track it on the Jobs page.")


@app.post("/library/extract-covers")
def library_extract_covers(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import extract_embedded_covers
    tasks.run_task("extract_covers", f"Extract embedded covers (folder {folder_id})",
                   lambda ctx: extract_embedded_covers(folder_id, ctx=ctx))
    return _toast_response("/library", "Cover extraction started — track it on the Queue page.")


@app.post("/library/replaygain")
def library_replaygain(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import recompute_replaygain
    tasks.run_task("replaygain", f"Recompute ReplayGain (folder {folder_id})",
                   lambda ctx: recompute_replaygain(folder_id, ctx=ctx))
    return _toast_response("/library", "ReplayGain recompute started (requires rsgain/loudgain).")


@app.post("/library/verify-integrity")
def library_verify_integrity(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import verify_integrity
    tasks.run_task("verify_integrity", f"Verify integrity (folder {folder_id})",
                   lambda ctx: verify_integrity(folder_id, ctx=ctx))
    return _toast_response("/library", "Integrity check started — track it on the Queue page.")


@app.post("/library/find-missing-tracks")
def library_find_missing_tracks(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import find_missing_tracks
    tasks.run_task("find_missing_tracks", f"Find missing tracks (folder {folder_id})",
                   lambda ctx: find_missing_tracks(folder_id, ctx=ctx))
    return _toast_response("/library", "Missing-track scan started — results land on the Library page's Incomplete tab.")


@app.post("/library/find-duplicates")
def library_find_duplicates(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import find_duplicates
    tasks.run_task("find_duplicates", f"Find duplicates (folder {folder_id})",
                   lambda ctx: find_duplicates(folder_id, ctx=ctx))
    return _toast_response("/library", "Duplicate scan started (report only) — see the job log.")


@app.post("/library/prune")
def library_prune(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import prune_library
    tasks.run_task("prune", f"Prune junk & empty folders (folder {folder_id})",
                   lambda ctx: prune_library(folder_id, ctx=ctx))
    return _toast_response("/library", "Prune started — junk files and empty folders will be removed.")


@app.post("/library/cleanup")
def library_cleanup(
    request: Request,
    _: None = Depends(require_auth),
    folder_id: int = Form(...),
    apply: str | None = Form(None),
):
    """Report (default) or apply library cleanup: merge edition-suffix twin
    folders, dedupe covers, quarantine dead folders/leftovers. Nothing deleted."""
    from .library.actions import cleanup_library
    do_apply = bool(apply)
    if do_apply and (msg := _batch_guard()):
        return _toast_response("/library", msg, "error")
    mode = "apply" if do_apply else "report"
    tasks.run_task("cleanup", f"Library cleanup [{mode}] (folder {folder_id})",
                   lambda ctx: cleanup_library(folder_id, ctx=ctx, apply=do_apply))
    return _toast_response(
        "/library",
        "Cleanup (apply) started — twins merged, leftovers quarantined, nothing deleted."
        if do_apply else "Cleanup report started — see the job log; nothing is changed.",
    )


@app.post("/library/scan-health")
def library_scan_health(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    """Refresh the Completions page's covers/genres snapshot (read-only)."""
    from .library.actions import scan_health
    job_id = tasks.run_task("health_scan", f"Health scan (folder {folder_id})",
                            lambda ctx: scan_health(folder_id, ctx=ctx))
    return _toast_response("/completions", "Health scan started — sections refresh when it finishes.", job_id=job_id)


@app.post("/library/validate-tags")
def library_validate_tags(request: Request, _: None = Depends(require_auth), folder_id: int = Form(...)):
    from .library.actions import validate_tags
    tasks.run_task("validate_tags", f"Validate tags (folder {folder_id})",
                   lambda ctx: validate_tags(folder_id, ctx=ctx))
    return _toast_response("/library", "Tag validation started (report only) — see the job log.")


def _batch_guard() -> str | None:
    """Refuse a new batch while another background task is running.

    Batches move files around; two running at once on the same folder could
    race. Ingest jobs are fine — the pipeline worker is independent — and so
    is a running "retag" job: it only *enqueues* ingest rows (the file work
    happens in the pipeline worker, which this guard already ignores).
    """
    with session() as s:
        running = s.exec(select(Job).where(
            Job.status == JobStatus.running,
            Job.kind.not_in(["ingest", "retag"]),
        )).first()
    if running:
        return f"'{running.original_name}' is still running — wait for it to finish first."
    return None


@app.get("/library/incomplete")
def library_incomplete_redirect(request: Request, _: None = Depends(require_auth)):
    """The old Incomplete tab lives on the Completions page now."""
    return RedirectResponse("/completions#missing-tracks", status_code=308)


@app.post("/library/incomplete/{row_id}/delete")
def library_incomplete_delete(row_id: int, request: Request, _: None = Depends(require_auth)):
    from .models import IncompleteAlbum
    with session() as s:
        row = s.get(IncompleteAlbum, row_id)
        if row:
            s.delete(row)
            s.commit()
    return _toast_response("/completions", "Dismissed.")


# ---------------------------------------------------------------------------
# Completions page — library-health report
# ---------------------------------------------------------------------------

# Section name -> fragment template. Live sections query the DB on render;
# snapshot sections read rows refreshed by background jobs (find_missing_tracks
# for IncompleteAlbum, scan_health for HealthItem).
_COMPLETIONS_SECTIONS = {
    "missing-tracks": "_completions_missing_tracks.html",
    "duplicates": "_completions_duplicates.html",
    "no-lyrics": "_completions_no_lyrics.html",
    "covers": "_completions_covers.html",
    "genres": "_completions_genres.html",
    "untagged": "_completions_untagged.html",
    "tag-problems": "_completions_tag_problems.html",
}


def _completions_counts() -> dict:
    """Cheap summary counts for the Completions tile row."""
    from .models import HealthItem, IncompleteAlbum
    with session() as s:
        total = s.exec(select(func.count(Track.id))).one() or 0
        with_lyrics = s.exec(
            select(func.count(Track.id)).where(Track.has_lyrics == True)  # noqa: E712
        ).one() or 0
        untagged = s.exec(
            select(func.count(Track.id)).where(
                or_(Track.mb_track_id.is_(None), Track.mb_album_id.is_(None))
            )
        ).one() or 0
        incomplete = s.exec(select(func.count(IncompleteAlbum.id))).one() or 0
        missing_cover = s.exec(
            select(func.count(HealthItem.id)).where(HealthItem.category == "missing_cover")
        ).one() or 0
        missing_genre = s.exec(
            select(func.count(HealthItem.id)).where(HealthItem.category == "missing_genre")
        ).one() or 0
        health_checked_at = s.exec(select(func.max(HealthItem.checked_at))).one()
        incomplete_checked_at = s.exec(select(func.max(IncompleteAlbum.checked_at))).one()
        no_lyrics = total - with_lyrics

        from .library.actions import duplicate_groups, tag_problems
        tracks = s.exec(select(Track)).all()
        dup_groups = duplicate_groups(tracks)
        problems = tag_problems(tracks)

    def pct(part: int) -> int:
        return int(round(100 * part / total)) if total else 100

    return {
        "total": total,
        "no_lyrics": no_lyrics,
        "lyrics_pct": pct(with_lyrics),
        "untagged": untagged,
        "tagged_pct": pct(total - untagged),
        "incomplete": incomplete,
        "missing_cover": missing_cover,
        "covers_pct": pct(total - missing_cover),
        "missing_genre": missing_genre,
        "genres_pct": pct(total - missing_genre),
        "dup_groups": len(dup_groups),
        "dup_files": sum(len(g) for g in dup_groups),
        "tag_problems": len(problems),
        "health_checked_at": health_checked_at,
        "incomplete_checked_at": incomplete_checked_at,
    }


@app.get("/completions", response_class=HTMLResponse)
def completions(request: Request, _: None = Depends(require_auth)):
    """Library-health report: gaps and duplicates, live + snapshot sections."""
    with session() as s:
        folders = s.exec(
            select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)
        ).all()
    return templates.TemplateResponse(request, "completions.html", {
        "request": request,
        "counts": _completions_counts(),
        "folders": folders,
        "sections": list(_COMPLETIONS_SECTIONS),
        "active_page": "completions",
    })


@app.get("/completions/section/{name}", response_class=HTMLResponse)
def completions_section(
    name: str,
    request: Request,
    _: None = Depends(require_auth),
    page: int = 1,
    page_size: int = 50,
):
    """HTMX fragment: one Completions section, paginated."""
    from .models import HealthItem, IncompleteAlbum
    template = _COMPLETIONS_SECTIONS.get(name)
    if template is None:
        raise HTTPException(404)
    page = max(1, page)
    if page_size not in _LIBRARY_PAGE_SIZES:
        page_size = 50
    offset = (page - 1) * page_size
    ctx: dict[str, Any] = {"request": request, "name": name, "page": page,
                           "page_size": page_size}

    with session() as s:
        folders = s.exec(
            select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)
        ).all()
        ctx["folders"] = folders
        ctx["folder_labels"] = {f.id: (f.label or f.path) for f in folders}

        if name == "missing-tracks":
            total = s.exec(select(func.count(IncompleteAlbum.id))).one() or 0
            ctx["rows"] = s.exec(
                select(IncompleteAlbum)
                .order_by(IncompleteAlbum.artist, IncompleteAlbum.album)
                .offset(offset).limit(page_size)
            ).all()
        elif name in ("covers", "genres"):
            cat = "missing_cover" if name == "covers" else "missing_genre"
            total = s.exec(
                select(func.count(HealthItem.id)).where(HealthItem.category == cat)
            ).one() or 0
            ctx["rows"] = s.exec(
                select(HealthItem).where(HealthItem.category == cat)
                .order_by(HealthItem.path)
                .offset(offset).limit(page_size)
            ).all()
        elif name == "no-lyrics":
            cond = Track.has_lyrics == False  # noqa: E712
            total = s.exec(select(func.count(Track.id)).where(cond)).one() or 0
            ctx["rows"] = s.exec(
                select(Track).where(cond)
                .order_by(Track.album_artist, Track.album, Track.track_num)
                .offset(offset).limit(page_size)
            ).all()
        elif name == "untagged":
            cond = or_(Track.mb_track_id.is_(None), Track.mb_album_id.is_(None))
            total = s.exec(select(func.count(Track.id)).where(cond)).one() or 0
            ctx["rows"] = s.exec(
                select(Track).where(cond)
                .order_by(Track.album_artist, Track.album, Track.track_num)
                .offset(offset).limit(page_size)
            ).all()
        elif name == "duplicates":
            from .library.actions import duplicate_album_groups, duplicate_groups
            tracks = s.exec(select(Track)).all()
            groups = duplicate_groups(tracks)
            total = len(groups)
            ctx["groups"] = groups[offset:offset + page_size]
            ctx["album_groups"] = duplicate_album_groups(tracks)[:25]
        else:  # tag-problems
            from .library.actions import tag_problems
            tracks = s.exec(select(Track)).all()
            problems = tag_problems(tracks)
            total = len(problems)
            ctx["rows"] = problems[offset:offset + page_size]

    ctx["total"] = total
    ctx["total_pages"] = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(request, template, ctx)


@app.post("/completions/item/{row_id}/delete")
def completions_item_delete(row_id: int, request: Request, _: None = Depends(require_auth)):
    """Dismiss one snapshot health finding (advisory only, never touches files)."""
    from .models import HealthItem
    with session() as s:
        row = s.get(HealthItem, row_id)
        if row:
            s.delete(row)
            s.commit()
    return _toast_response("/completions", "Dismissed.")


@app.get("/api/mb-search", response_class=HTMLResponse)
def api_mb_search(
    request: Request,
    _: None = Depends(require_auth),
    title: str = "",
    artist: str = "",
    album: str = "",
    mbid: str = "",
    job_id: int = 0,
):
    """HTMX partial: search MusicBrainz from the review page.

    Supports three input modes:
    * a direct MusicBrainz URL / ID (``mbid``) — resolved via
      ``candidates_from_mbid`` (recording → its releases, release → its tracks);
    * title + optional artist + album fields;
    * title only, in which case the job's known artist and album are seeded so
      results stay scoped to the right artist for common titles.
    """
    try:
        return _api_mb_search_inner(request, title, artist, album, mbid, job_id)
    except Exception as e:
        log.warning("mb-search failed: %s", e)
        return templates.TemplateResponse(request, "_mb_search_results.html", {
            "request": request, "job_id": job_id, "cands": [], "searched": True,
            "search_error": "MusicBrainz search failed — network error. Try again.",
        })


def _api_mb_search_inner(request: Request, title: str, artist: str, album: str, mbid: str, job_id: int):
    if mbid.strip():
        cands = mbq.candidates_from_mbid(mbid.strip(), title_hint=title or None)
        searched = True
    else:
        seed_artist = artist.strip() or None
        seed_album = album.strip() or None
        if job_id and not (seed_artist and seed_album):
            with session() as s:
                job = s.get(Job, job_id)
                if job:
                    stored = job.chosen_tags_json or {}
                    stored_artists = stored.get("artists")
                    seed_artist = seed_artist or stored.get("artist_display") or (
                        stored_artists[0]
                        if isinstance(stored_artists, list) and stored_artists else None
                    )
                    seed_album = seed_album or stored.get("album")
        cands = (
            mbq.search_candidates(
                title=title, artist=seed_artist, album=seed_album, limit=10,
                raise_on_error=True,
            )
            if title.strip()
            else []
        )
        searched = bool(title.strip())

    return templates.TemplateResponse(request, "_mb_search_results.html", {
        "request": request,
        "job_id": job_id,
        "cands": [
            {
                "recording_id": c.recording_id,
                "release_id": c.release_id,
                "score": c.score,
                "title": c.raw_recording.get("title", ""),
                "artist": c.raw_recording.get("artist-credit-phrase", ""),
                "album": c.raw_release.get("title", ""),
            }
            for c in cands
        ],
        "searched": searched,
    })


@app.get("/docs", response_class=HTMLResponse)
def docs(request: Request, _: None = Depends(require_auth)):
    return templates.TemplateResponse(request, "docs.html", {"request": request, "active_page": "docs"})


@app.get("/changes", response_class=HTMLResponse)
def changes(request: Request, _: None = Depends(require_auth)):
    """Recent file changes (tag write + move) with a per-row revert action."""
    with session() as s:
        rows = s.exec(select(FileChange).order_by(FileChange.id.desc()).limit(200)).all()
    return templates.TemplateResponse(
        request, "changes.html", {"request": request, "changes": rows, "active_page": "changes"}
    )


@app.post("/changes/{change_id}/revert")
def changes_revert(change_id: int, request: Request, _: None = Depends(require_auth)):
    from .library.revert import revert_change

    ok, message = revert_change(change_id)
    return _toast_response("/changes", message, "success" if ok else "error")


@app.post("/changes/{change_id}/move-back")
def changes_move_back(change_id: int, request: Request, _: None = Depends(require_auth)):
    from .library.revert import move_back

    ok, message = move_back(change_id)
    return _toast_response("/changes", message, "success" if ok else "error")


@app.post("/changes/clear")
def changes_clear(request: Request, _: None = Depends(require_auth)):
    """Delete all FileChange audit rows (the files themselves are untouched)."""
    with session() as s:
        rows = s.exec(select(FileChange)).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return _toast_response("/changes", f"Cleared {len(rows)} change record(s).")


@app.post("/settings/clear-scan-filters")
def settings_clear_scan_filters(request: Request, _: None = Depends(require_auth)):
    """Empty all three scan filter lists (patterns, excluded dirs, excluded files)."""
    cfg = settings()
    n = (
        len(cfg.scan_filter_patterns)
        + len(cfg.scan_exclude_dirs)
        + len(cfg.scan_exclude_files)
    )
    store().update(
        {"scan_filter_patterns": [], "scan_exclude_dirs": [], "scan_exclude_files": []}
    )
    return _toast_response("/settings", f"Cleared {n} scan filter entries.")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@app.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request, _: None = Depends(require_auth)):
    with session() as s:
        rows = s.exec(select(ScheduledTask).order_by(ScheduledTask.id)).all()
        folders = s.exec(select(LibraryFolder).order_by(LibraryFolder.priority, LibraryFolder.id)).all()
    return templates.TemplateResponse(request, "schedule.html", {
        "request": request,
        "schedules": rows,
        "folders": folders,
        "task_types": scheduler.TASK_TYPES,
        # Cron expressions fire in the display timezone (scheduler._cron_tz),
        # not UTC — tell the user which one so "0 6 * * *" means what it says.
        "tz_name": getattr(_local_tz(), "key", None) or "UTC",
        "active_page": "schedule",
    })


@app.post("/schedule")
def schedule_create(
    request: Request,
    _: None = Depends(require_auth),
    name: str = Form(...),
    cron: str = Form(...),
    task_type: str = Form(...),
    folder_id: str = Form(default=""),
    source_path: str = Form(default=""),
    dry_run: str | None = Form(None),
    apply: str | None = Form(None),
):
    cron = cron.strip()
    if task_type not in scheduler.TASK_TYPES:
        return _toast_response("/schedule", f"Unknown task type: {task_type}", "error")
    if not scheduler.is_valid_cron(cron):
        return _toast_response("/schedule", f"Invalid cron expression: {cron}", "error")
    params: dict = {}
    if task_type in ("scan", "organize", "fetch_lyrics", "fetch_covers", "cleanup"):
        if not folder_id.strip().isdigit():
            return _toast_response("/schedule", "Pick a library folder for this task type.", "error")
        params["folder_id"] = int(folder_id)
    if task_type == "retag":
        if not source_path.strip():
            return _toast_response("/schedule", "A source path is required for re-tag.", "error")
        params["source_path"] = source_path.strip()
        params["dry_run"] = bool(dry_run)
    if task_type == "cleanup":
        params["apply"] = bool(apply)
    with session() as s:
        t = ScheduledTask(
            name=name.strip() or scheduler.TASK_TYPES[task_type],
            cron=cron,
            task_type=task_type,
            params_json=params,
            next_run_at=scheduler.next_run(cron),
        )
        s.add(t)
        s.commit()
    return _toast_response("/schedule", "Schedule created.")


@app.post("/schedule/{task_id}/delete")
def schedule_delete(task_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        t = s.get(ScheduledTask, task_id)
        if t:
            s.delete(t)
            s.commit()
    return _toast_response("/schedule", "Schedule deleted.")


@app.post("/schedule/{task_id}/toggle")
def schedule_toggle(task_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        t = s.get(ScheduledTask, task_id)
        if not t:
            raise HTTPException(404)
        t.enabled = not t.enabled
        t.next_run_at = scheduler.next_run(t.cron) if t.enabled else None
        s.add(t)
        s.commit()
        state = "enabled" if t.enabled else "disabled"
    return _toast_response("/schedule", f"Schedule {state}.")


@app.post("/schedule/{task_id}/run-now")
def schedule_run_now(task_id: int, request: Request, _: None = Depends(require_auth)):
    with session() as s:
        t = s.get(ScheduledTask, task_id)
        if not t:
            raise HTTPException(404)
        # Same guard the scheduler tick applies: two concurrent same-kind
        # file-moving batches racing over one folder is exactly what it exists
        # to prevent, and "Run now" must not be a bypass.
        already_running = s.exec(
            select(Job).where(
                Job.kind == t.task_type, Job.status == JobStatus.running
            )
        ).first()
        if already_running is not None:
            return _toast_response(
                "/schedule",
                f"A {t.task_type} task is still running — not starting another.",
                "error",
            )
    try:
        scheduler.run_task_by_type(t)
    except Exception as e:
        return _toast_response("/schedule", f"Run failed: {e}", "error")
    with session() as s:
        row = s.get(ScheduledTask, task_id)
        if row:
            row.last_run_at = now_utc()
            row.last_status = "ok (manual)"
            s.add(row)
            s.commit()
    return _toast_response("/schedule", "Task started — track it on the Jobs page.")


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------


@app.get("/backup/download")
def backup_download(request: Request, _: None = Depends(require_auth)):
    from .backup import create_backup
    try:
        path = create_backup()
    except Exception as e:
        return _toast_response("/settings", f"Backup failed: {e}", "error")
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@app.post("/backup/restore")
async def backup_restore(
    request: Request,
    _: None = Depends(require_auth),
    bundle: UploadFile = File(...),
):
    from .backup import restore_bundle

    # Refuse while jobs are in flight — the restore swaps the DB underneath them.
    with session() as s:
        active = s.exec(select(func.count(Job.id)).where(
            Job.status.in_(list(_ACTIVE_JOB_STATUSES))
        )).one() or 0
    if active:
        return _toast_response(
            "/settings", f"Refusing to restore while {active} job(s) are active.", "error"
        )

    import tempfile as _tmp
    # Stream to disk in chunks (never load the whole upload into memory) and
    # cap the total so a giant upload can't exhaust memory or disk.
    max_backup = 2 * 1024 * 1024 * 1024  # 2 GiB
    written = 0
    with _tmp.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
        tmp_path = Path(f.name)
        while chunk := await bundle.read(1 << 20):
            written += len(chunk)
            if written > max_backup:
                f.close()
                tmp_path.unlink(missing_ok=True)
                return _toast_response(
                    "/settings", "Restore refused: backup exceeds 2 GiB.", "error"
                )
            f.write(chunk)
    try:
        message = restore_bundle(tmp_path)
    except ValueError as e:
        return _toast_response("/settings", f"Restore refused: {e}", "error")
    finally:
        tmp_path.unlink(missing_ok=True)
    return _toast_response("/settings", message)
