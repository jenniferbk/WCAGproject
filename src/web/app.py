"""FastAPI web application for document accessibility remediation.

Provides:
- User registration and authentication (local + OAuth)
- File upload with per-user usage limits
- Job status tracking (per-user isolation)
- Report viewing and remediated file download
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import logging
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import io
import zipfile

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest
from src.web.billing import (
    WebhookError,
    create_checkout_session,
    get_packs_for_display,
    get_user_transactions,
    handle_webhook,
    init_billing_db,
)
from src.web.cost_cap import (
    check_can_submit,
    current_status as cost_cap_status,
    ensure_cost_column,
    record_job_cost,
)
from src.web.observability import RequestIdMiddleware, configure_logging
from src.web.queue import enqueue_job as arq_enqueue_job, get_backend as queue_backend
from src.web.retention import run_cleanup as run_retention_cleanup, start_background_loop as start_retention_loop
from src.web.user_caps import check_user_caps
from src.web.auth import (
    clear_session_cookie,
    create_reset_token,
    create_token,
    hash_password,
    set_session_cookie,
    verify_password,
)
from src.web.email import send_job_complete_email, send_job_failed_email, send_password_reset_email
from src.web.jobs import (
    create_job,
    delete_jobs,
    get_deletable_jobs,
    get_job,
    get_jobs_by_ids,
    init_db,
    list_jobs,
    list_jobs_by_batch,
    update_job,
)
from src.web.middleware import get_current_user, require_admin, require_user
from src.web.rate_limit import rate_limit
from src.web.users import (
    User,
    add_pages,
    clear_reset_token,
    create_user,
    deduct_pages,
    get_user,
    get_user_by_email,
    get_user_by_oauth,
    get_user_by_reset_token,
    init_users_db,
    list_users,
    refund_pages,
    reset_documents_used,
    set_reset_token,
    update_user,
)

logger = logging.getLogger(__name__)

# ── Rate limiters ────────────────────────────────────────────────
_login_limit = rate_limit(10, 60)                # 10/min per IP
_register_limit = rate_limit(5, 3600)            # 5/hour per IP
_forgot_password_limit = rate_limit(5, 3600)     # 5/hour per IP
_reset_password_limit = rate_limit(10, 3600)     # 10/hour per IP
_upload_limit = rate_limit(20, 3600)             # 20/hour per user (key set at endpoint)
_checkout_limit = rate_limit(10, 3600)           # 10/hour per user
_general_limit = rate_limit(120, 60)             # 120/min per IP

UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "output"
STATIC_DIR = Path(__file__).parent / "static"

def _dispatch_job(job_id: str) -> None:
    """Dispatch a job for processing. Routes to ARQ when QUEUE_BACKEND=arq,
    otherwise falls back to the historical threading.Thread daemon."""
    if queue_backend() == "arq":
        try:
            arq_enqueue_job(job_id)
            return
        except Exception:
            logger.exception(
                "ARQ enqueue failed for job %s — falling back to threading", job_id,
            )
    thread = threading.Thread(target=_process_job, args=(job_id,), daemon=True)
    thread.start()


# Limit concurrent remediation jobs. Raised cautiously via env var.
# Defaults to 1 — historical safe value for Anthropic Tier-1 rate limits
# (Sonnet ~30k ITPM). Real-world bottleneck is usually ITPM, not CPU,
# so monitor `usage` headers when raising this. Memory at 12GB ARM
# also caps practical concurrency around 3 (each in-flight job holds
# parsed PDF + Gemini/Claude payloads + iText/veraPDF subprocesses).
def _read_max_concurrent_jobs() -> int:
    raw = os.environ.get("MAX_CONCURRENT_JOBS", "").strip()
    if not raw:
        return 1
    try:
        n = int(raw)
        if n < 1:
            logger.warning("MAX_CONCURRENT_JOBS=%r < 1, falling back to 1", raw)
            return 1
        return n
    except ValueError:
        logger.warning("MAX_CONCURRENT_JOBS=%r is not an integer, falling back to 1", raw)
        return 1

_MAX_CONCURRENT_JOBS = _read_max_concurrent_jobs()
_processing_semaphore = threading.Semaphore(_MAX_CONCURRENT_JOBS)
logger.info("Concurrent job limit: %d", _MAX_CONCURRENT_JOBS)

app = FastAPI(title="A11y Remediation", version="0.1.0")
app.add_middleware(RequestIdMiddleware)
configure_logging()


@app.on_event("startup")
def startup():
    init_db()
    init_users_db()
    init_billing_db()
    ensure_cost_column()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _recover_stuck_jobs()
    start_retention_loop(UPLOAD_DIR, OUTPUT_DIR)


def _recover_stuck_jobs() -> None:
    """Re-queue jobs that were interrupted by a server restart.

    Any job in 'processing' state was mid-flight when the server died.
    Reset it to 'queued' and re-enqueue it along with any other queued jobs.
    """
    from src.web.jobs import _get_conn

    conn = _get_conn()
    # Reset processing -> queued
    stuck = conn.execute(
        "SELECT id, filename FROM jobs WHERE status = 'processing'"
    ).fetchall()
    for row in stuck:
        logger.info("Recovering stuck job %s: %s", row[0][:8], row[1])
        conn.execute(
            "UPDATE jobs SET status = 'queued', phase = 'waiting' WHERE id = ?",
            (row[0],),
        )
    conn.commit()

    # Re-enqueue all queued jobs (router picks ARQ or threading)
    queued = conn.execute(
        "SELECT id, filename FROM jobs WHERE status = 'queued' ORDER BY created_at ASC"
    ).fetchall()
    for row in queued:
        logger.info("Re-enqueuing job %s: %s", row[0][:8], row[1])
        _dispatch_job(row[0])

    if queued:
        logger.info("Recovered %d jobs (%d were stuck)", len(queued), len(stuck))


def _check_admin_promotion(user: User) -> User:
    """Auto-promote user to admin if their email is in ADMIN_EMAILS."""
    admin_emails_raw = os.environ.get("ADMIN_EMAILS", "jennifer.b.kleiman@gmail.com,a11yremediate@gmail.com")
    admin_emails = {e.strip().lower() for e in admin_emails_raw.split(",") if e.strip()}
    if user.email.lower() in admin_emails and not user.is_admin:
        updated = update_user(user.id, is_admin=True)
        if updated:
            return updated
    return user


@app.get("/api/health")
async def health():
    """Liveness + readiness check.

    Public endpoint suitable for uptime monitors. Returns minimal information
    by design — admin-only endpoints (cost-status, retention) carry the
    sensitive operational details.
    """
    from src.web.jobs import _get_conn

    status = "ok"
    db_status = "ok"
    queue_depth = -1
    processing_depth = -1

    try:
        conn = _get_conn()
        conn.execute("SELECT 1").fetchone()
        queue_row = conn.execute(
            "SELECT status, COUNT(*) FROM jobs WHERE status IN ('queued', 'processing') GROUP BY status"
        ).fetchall()
        counts = {row[0]: row[1] for row in queue_row}
        queue_depth = counts.get("queued", 0)
        processing_depth = counts.get("processing", 0)
    except Exception as e:
        logger.warning("Health check DB error: %s", e)
        db_status = "error"
        status = "degraded"

    disk_free_mb = -1
    try:
        usage = shutil.disk_usage(str(UPLOAD_DIR))
        disk_free_mb = usage.free // (1024 * 1024)
    except Exception:
        pass

    return {
        "status": status,
        "db": db_status,
        "queue": {"queued": queue_depth, "processing": processing_depth},
        "disk_free_mb": disk_free_mb,
        "version": app.version,
    }


# ── Static frontend ──────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text())


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "\n"
        "Sitemap: https://remediate.jenkleiman.com/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
async def sitemap_xml():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        "    <loc>https://remediate.jenkleiman.com/</loc>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "  <url>\n"
        "    <loc>https://remediate.jenkleiman.com/?info=about</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.8</priority>\n"
        "  </url>\n"
        "  <url>\n"
        "    <loc>https://remediate.jenkleiman.com/?info=contact</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.6</priority>\n"
        "  </url>\n"
        "  <url>\n"
        "    <loc>https://remediate.jenkleiman.com/?info=terms</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.3</priority>\n"
        "  </url>\n"
        "  <url>\n"
        "    <loc>https://remediate.jenkleiman.com/?info=privacy</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.3</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


# ── Auth endpoints ───────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(data: dict, _rate=Depends(_register_limit)):
    """Register a new user with email + password."""
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()

    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "Email and password are required"})

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return JSONResponse(status_code=400, content={"error": "Invalid email address"})

    if len(password) < 8:
        return JSONResponse(status_code=400, content={"error": "Password must be at least 8 characters"})

    existing = get_user_by_email(email)
    if existing:
        return JSONResponse(status_code=409, content={"error": "An account with this email already exists"})

    email_opt_in = bool(data.get("email_opt_in", False))

    hashed = hash_password(password)
    user = create_user(email=email, password_hash=hashed, display_name=display_name or email.split("@")[0], email_opt_in=email_opt_in)
    user = _check_admin_promotion(user)

    token = create_token(user.id, user.email)
    response = JSONResponse(content={"user": user.to_dict()})
    set_session_cookie(response, token)
    return response


@app.post("/api/auth/login")
async def login(data: dict, _rate=Depends(_login_limit)):
    """Login with email + password."""
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "Email and password are required"})

    user = get_user_by_email(email)
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return JSONResponse(status_code=401, content={"error": "Invalid email or password"})

    user = _check_admin_promotion(user)
    token = create_token(user.id, user.email)
    response = JSONResponse(content={"user": user.to_dict()})
    set_session_cookie(response, token)
    return response


@app.post("/api/auth/logout")
async def logout(_rate=Depends(_general_limit)):
    """Clear the session cookie."""
    response = JSONResponse(content={"ok": True})
    clear_session_cookie(response)
    return response


@app.get("/api/auth/me")
async def me(user: User | None = Depends(get_current_user), _rate=Depends(_general_limit)):
    """Return current user info, or 401 if not authenticated."""
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    return {"user": user.to_dict()}


@app.patch("/api/auth/me")
async def update_me(data: dict, user: User | None = Depends(get_current_user)):
    """Update current user's preferences."""
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    allowed = {"email_opt_in", "display_name"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return JSONResponse(status_code=400, content={"error": "No valid fields to update"})
    if "email_opt_in" in updates:
        updates["email_opt_in"] = int(bool(updates["email_opt_in"]))
    updated = update_user(user.id, **updates)
    if not updated:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    return {"user": updated.to_dict()}


@app.post("/api/auth/forgot-password")
async def forgot_password(data: dict, _rate=Depends(_forgot_password_limit)):
    """Send a password reset email. Always returns 200 to prevent email enumeration."""
    email = (data.get("email") or "").strip().lower()
    if not email:
        return JSONResponse(status_code=400, content={"error": "Email is required"})

    user = get_user_by_email(email)
    # Only send if user exists and has a password (not OAuth-only)
    if user and user.password_hash:
        from src.web.email import SITE_URL
        token = create_reset_token()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        set_reset_token(user.id, token, expires_at)
        reset_url = f"{SITE_URL}/?reset={token}"
        send_password_reset_email(user.email, reset_url)

    return {"ok": True}


@app.post("/api/auth/reset-password")
async def reset_password(data: dict, _rate=Depends(_reset_password_limit)):
    """Reset a user's password using a valid reset token."""
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""

    if not token:
        return JSONResponse(status_code=400, content={"error": "Token is required"})
    if len(password) < 8:
        return JSONResponse(status_code=400, content={"error": "Password must be at least 8 characters"})

    user = get_user_by_reset_token(token)
    if not user:
        return JSONResponse(status_code=400, content={"error": "Invalid or expired reset token"})

    hashed = hash_password(password)
    update_user(user.id, password_hash=hashed)
    clear_reset_token(user.id)

    # Auto-login
    user = get_user(user.id)
    user = _check_admin_promotion(user)
    jwt_token = create_token(user.id, user.email)
    response = JSONResponse(content={"user": user.to_dict()})
    set_session_cookie(response, jwt_token)
    return response


# ── OAuth endpoints ──────────────────────────────────────────────

@app.get("/api/auth/google")
async def google_login():
    """Initiate Google OAuth. Requires GOOGLE_CLIENT_ID/SECRET env vars."""
    try:
        from src.web.oauth import google_oauth
        redirect_uri = "/api/auth/google/callback"
        url = await google_oauth.create_authorization_url(redirect_uri)
        return JSONResponse(content={"url": url})
    except Exception:
        return JSONResponse(status_code=501, content={"error": "Google OAuth not configured"})


@app.get("/api/auth/google/callback")
async def google_callback(code: str = "", state: str = ""):
    """Handle Google OAuth callback."""
    try:
        from src.web.oauth import handle_google_callback
        user = await handle_google_callback(code, state)
        user = _check_admin_promotion(user)
        token = create_token(user.id, user.email)
        # Redirect to home page with cookie set
        response = HTMLResponse('<script>window.location="/"</script>')
        set_session_cookie(response, token)
        return response
    except Exception as e:
        logger.exception("Google OAuth callback failed")
        return HTMLResponse(f'<script>window.location="/?error=oauth_failed"</script>')


@app.get("/api/auth/microsoft")
async def microsoft_login():
    """Initiate Microsoft OAuth. Requires MICROSOFT_CLIENT_ID/SECRET env vars."""
    try:
        from src.web.oauth import microsoft_oauth
        redirect_uri = "/api/auth/microsoft/callback"
        url = await microsoft_oauth.create_authorization_url(redirect_uri)
        return JSONResponse(content={"url": url})
    except Exception:
        return JSONResponse(status_code=501, content={"error": "Microsoft OAuth not configured"})


@app.get("/api/auth/microsoft/callback")
async def microsoft_callback(code: str = "", state: str = ""):
    """Handle Microsoft OAuth callback."""
    try:
        from src.web.oauth import handle_microsoft_callback
        user = await handle_microsoft_callback(code, state)
        user = _check_admin_promotion(user)
        token = create_token(user.id, user.email)
        response = HTMLResponse('<script>window.location="/"</script>')
        set_session_cookie(response, token)
        return response
    except Exception as e:
        logger.exception("Microsoft OAuth callback failed")
        return HTMLResponse(f'<script>window.location="/?error=oauth_failed"</script>')


# ── API endpoints (protected) ───────────────────────────────────

def _count_pages(file_path: str) -> int:
    """Count pages in a document for billing purposes.

    PDF: exact page count via PyMuPDF.
    PPTX: exact slide count via python-pptx.
    DOCX: heuristic based on word count (~275 words/page).
    """
    import math

    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(file_path)
        count = len(doc)
        doc.close()
        return max(count, 1)
    elif suffix == ".pptx":
        from pptx import Presentation
        prs = Presentation(file_path)
        return max(len(prs.slides), 1)
    elif suffix == ".docx":
        from docx import Document
        doc = Document(file_path)
        word_count = sum(len(p.text.split()) for p in doc.paragraphs)
        return max(math.ceil(word_count / 275), 1)
    return 1


@app.post("/api/upload")
async def upload_file(
    user: User = Depends(require_user),
    file: UploadFile = File(...),
    course_name: str = Form(""),
    department: str = Form(""),
    batch_id: str = Form(""),
    _rate=Depends(_upload_limit),
):
    """Upload a document for remediation. Requires authentication."""
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in (".docx", ".pdf", ".pptx", ".tex", ".ltx", ".zip"):
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: {suffix}. Accepts .docx, .pdf, .pptx, .tex, .zip"},
        )

    cost_status = check_can_submit()
    if not cost_status.allowed:
        if cost_status.reason == "kill_switch":
            msg = "Document processing is temporarily paused for maintenance. Please try again later."
        else:
            msg = "Document processing is temporarily paused — daily capacity reached. Please try again tomorrow."
        return JSONResponse(
            status_code=503,
            content={"error": msg, "reason": cost_status.reason},
        )

    cap_status = check_user_caps(user.id, is_admin=user.is_admin)
    if not cap_status.allowed:
        if cap_status.reason == "concurrent_cap":
            msg = (
                f"You already have {cap_status.concurrent_jobs} jobs in progress. "
                "Please wait for them to finish before uploading more."
            )
        else:
            msg = (
                f"You've submitted {cap_status.hourly_jobs} jobs in the last hour. "
                "Please try again later."
            )
        return JSONResponse(
            status_code=429,
            content={"error": msg, "reason": cap_status.reason, **cap_status.to_dict()},
        )

    # Read and check file size
    content = await file.read()
    max_bytes = user.max_file_size_mb * 1024 * 1024
    if suffix == ".zip":
        max_bytes = 50 * 1024 * 1024  # 50MB for zip uploads
    if len(content) > max_bytes:
        return JSONResponse(
            status_code=413,
            content={"error": f"File too large. Maximum size is {user.max_file_size_mb}MB"},
        )

    # Save file to disk first so we can count pages
    temp_job_id = __import__("uuid").uuid4().hex[:12]
    upload_path = UPLOAD_DIR / f"{temp_job_id}_{filename}"
    with open(upload_path, "wb") as f:
        f.write(content)

    # Count pages for billing
    try:
        page_count = _count_pages(str(upload_path))
    except Exception:
        page_count = 1

    # LaTeX conversions are free while the feature stabilizes in production.
    # We still record the page count on the job for visibility, but we do not
    # deduct from the user's balance.
    is_latex_free = Path(filename).suffix.lower() in (".tex", ".ltx", ".zip")

    # Atomically deduct pages from balance (skip for free LaTeX conversions)
    if not is_latex_free and not deduct_pages(user.id, page_count):
        upload_path.unlink(missing_ok=True)
        # Re-fetch user for current balance
        fresh_user = get_user(user.id)
        return JSONResponse(
            status_code=403,
            content={
                "error": "Insufficient pages",
                "pages_balance": fresh_user.pages_balance if fresh_user else 0,
                "page_count": page_count,
            },
        )

    # Create job record with page count
    job = create_job(filename, "", course_name, department, user_id=user.id, batch_id=batch_id, page_count=page_count)

    # Rename temp file to use real job ID
    final_path = UPLOAD_DIR / f"{job.id}_{filename}"
    upload_path.rename(final_path)

    update_job(job.id, original_path=str(final_path), status="queued")

    # Dispatch for processing (ARQ or threading per QUEUE_BACKEND)
    _dispatch_job(job.id)

    return {"job_id": job.id, "status": "queued", "filename": filename}


@app.get("/api/jobs")
async def get_jobs(user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """List jobs for the authenticated user, with queue position info."""
    jobs = list_jobs(user_id=user.id)
    job_dicts = [j.to_dict() for j in jobs]

    # Compute queue positions for queued jobs
    has_queued = any(j.status == "queued" for j in jobs)
    if has_queued:
        from src.web.jobs import _get_conn
        conn = _get_conn()
        # Global queue order (all users)
        queued_rows = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC"
        ).fetchall()
        queue_order = {row[0]: i + 1 for i, row in enumerate(queued_rows)}

        # Average processing time from recent completed jobs
        avg_row = conn.execute(
            "SELECT AVG(processing_time) FROM jobs WHERE status = 'completed' AND processing_time > 0 ORDER BY updated_at DESC LIMIT 20"
        ).fetchone()
        avg_time = avg_row[0] if avg_row and avg_row[0] else 180  # default 3 min

        # Check if there's currently a processing job ahead in queue
        has_processing = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'processing'"
        ).fetchone()[0] > 0

        for jd in job_dicts:
            if jd["status"] == "queued":
                pos = queue_order.get(jd["id"], 0)
                jd["queue_position"] = pos
                # Jobs ahead = position - 1, plus current processing job
                jobs_ahead = (pos - 1) + (1 if has_processing else 0)
                jd["estimated_wait_seconds"] = round(jobs_ahead * avg_time)

    return {"jobs": job_dicts}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Get status of a specific job. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return job.to_dict()


@app.get("/api/jobs/{job_id}/report")
async def get_report(job_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Get the HTML compliance report. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.report_path or not Path(job.report_path).exists():
        return JSONResponse(status_code=404, content={"error": "Report not available"})
    return HTMLResponse(Path(job.report_path).read_text())


@app.get("/api/jobs/{job_id}/download")
async def download_file(job_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Download the remediated document. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.output_path or not Path(job.output_path).exists():
        return JSONResponse(status_code=404, content={"error": "File not available"})

    return FileResponse(
        job.output_path,
        filename=Path(job.output_path).name,
        media_type="application/octet-stream",
    )


@app.get("/api/jobs/{job_id}/download-original")
async def download_original(job_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Download the original uploaded document. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.original_path or not Path(job.original_path).exists():
        return JSONResponse(status_code=404, content={"error": "File not available"})

    return FileResponse(
        job.original_path,
        filename=job.filename,
        media_type="application/octet-stream",
    )


@app.get("/api/jobs/{job_id}/download-accessible")
async def download_accessible(job_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Download the accessible HTML companion file. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.companion_path or not Path(job.companion_path).exists():
        return JSONResponse(status_code=404, content={"error": "Accessible version not available"})

    return FileResponse(
        job.companion_path,
        filename=Path(job.companion_path).name,
        media_type="text/html",
    )


def _cleanup_job_files(job) -> None:
    """Remove original, output, and report files from disk."""
    for path_str in (job.original_path, job.output_path, job.report_path):
        if path_str:
            p = Path(path_str)
            if p.exists():
                p.unlink(missing_ok=True)
    # Remove output directory (data/output/{job_id}/)
    job_output_dir = OUTPUT_DIR / job.id
    if job_output_dir.exists() and job_output_dir.is_dir():
        shutil.rmtree(job_output_dir, ignore_errors=True)


@app.delete("/api/jobs/{job_id}")
async def delete_single_job(job_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Delete a single job. Cannot delete queued/processing jobs."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if job.status in ("queued", "processing"):
        return JSONResponse(status_code=409, content={"error": "Cannot delete a job that is still processing"})
    _cleanup_job_files(job)
    delete_jobs([job_id], user.id)
    return {"ok": True}


@app.post("/api/jobs/bulk-delete")
async def bulk_delete_jobs(data: dict, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Bulk delete jobs. Skips queued/processing."""
    job_ids = data.get("job_ids")
    if not job_ids or not isinstance(job_ids, list):
        return JSONResponse(status_code=400, content={"error": "job_ids must be a non-empty list"})
    # Cleanup files before deleting DB records
    deletable = get_deletable_jobs(job_ids, user.id)
    for job in deletable:
        _cleanup_job_files(job)
    count = delete_jobs(job_ids, user.id)
    return {"deleted": count}


@app.post("/api/jobs/download-zip")
async def download_zip(data: dict, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Download a ZIP of remediated files for the given job IDs."""
    job_ids = data.get("job_ids")
    if not job_ids or not isinstance(job_ids, list):
        return JSONResponse(status_code=400, content={"error": "job_ids must be a non-empty list"})

    jobs = get_jobs_by_ids(job_ids, user.id)
    # Only include completed jobs with existing output files
    downloadable = [
        j for j in jobs
        if j.status == "completed" and j.output_path and Path(j.output_path).exists()
    ]
    if not downloadable:
        return JSONResponse(status_code=404, content={"error": "No downloadable files found"})

    buf = io.BytesIO()
    used_names: dict[str, int] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in downloadable:
            name = Path(job.output_path).name
            if name in used_names:
                used_names[name] += 1
                stem = Path(name).stem
                suffix = Path(name).suffix
                name = f"{stem}_{used_names[name]}{suffix}"
            else:
                used_names[name] = 0
            zf.write(job.output_path, name)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=remediated_files.zip"},
    )


@app.get("/api/batches/{batch_id}")
async def get_batch(batch_id: str, user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Get aggregate stats for a batch of jobs."""
    jobs = list_jobs_by_batch(batch_id, user_id=user.id)
    if not jobs:
        return JSONResponse(status_code=404, content={"error": "Batch not found"})

    total = len(jobs)
    completed = sum(1 for j in jobs if j.status == "completed")
    failed = sum(1 for j in jobs if j.status == "failed")
    processing = sum(1 for j in jobs if j.status in ("queued", "processing"))

    return {
        "batch_id": batch_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "processing": processing,
        "jobs": [j.to_dict() for j in jobs],
    }


# ── Admin endpoints ──────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(admin: User = Depends(require_admin)):
    """List all users with usage stats."""
    users = list_users()
    return {"users": [u.to_dict() for u in users]}


@app.get("/api/admin/cost-status")
async def admin_cost_status(admin: User = Depends(require_admin)):
    """Current cost-cap status: today's spend, weekly spend, configured caps, kill switch."""
    return cost_cap_status().to_dict()


@app.post("/api/admin/retention/cleanup")
async def admin_retention_cleanup(admin: User = Depends(require_admin)):
    """Run a one-shot retention cleanup pass and return the report.

    Use for ad-hoc disk-space recovery or to verify retention is working.
    The background loop runs this automatically every RETENTION_INTERVAL_HOURS.
    """
    report = run_retention_cleanup(UPLOAD_DIR, OUTPUT_DIR)
    return report.to_dict()


@app.get("/api/admin/users/{user_id}")
async def admin_get_user(user_id: str, admin: User = Depends(require_admin)):
    """Get a single user's details."""
    user = get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    return {"user": user.to_dict()}


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: str, data: dict, admin: User = Depends(require_admin)):
    """Update user fields (tier, max_documents, max_file_size_mb, is_admin, pages_balance)."""
    user = get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})

    allowed_fields = {"tier", "max_documents", "max_file_size_mb", "is_admin", "pages_balance"}
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return JSONResponse(status_code=400, content={"error": "No valid fields to update"})

    # Validate
    if "tier" in updates and updates["tier"] not in ("free", "paid"):
        return JSONResponse(status_code=400, content={"error": "tier must be 'free' or 'paid'"})
    if "max_documents" in updates:
        if not isinstance(updates["max_documents"], int) or updates["max_documents"] < 0:
            return JSONResponse(status_code=400, content={"error": "max_documents must be a non-negative integer"})
    if "max_file_size_mb" in updates:
        if not isinstance(updates["max_file_size_mb"], int) or updates["max_file_size_mb"] < 1:
            return JSONResponse(status_code=400, content={"error": "max_file_size_mb must be a positive integer"})
    if "is_admin" in updates:
        if not isinstance(updates["is_admin"], bool):
            return JSONResponse(status_code=400, content={"error": "is_admin must be a boolean"})
    if "pages_balance" in updates:
        if not isinstance(updates["pages_balance"], int) or updates["pages_balance"] < 0:
            return JSONResponse(status_code=400, content={"error": "pages_balance must be a non-negative integer"})

    updated = update_user(user_id, **updates)
    return {"user": updated.to_dict()}


@app.post("/api/admin/users/{user_id}/reset-usage")
async def admin_reset_usage(user_id: str, admin: User = Depends(require_admin)):
    """Reset a user's documents_used and pages_used to 0."""
    user = get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})

    updated = reset_documents_used(user_id)
    # Also reset pages_used
    updated = update_user(user_id, pages_used=0)
    return {"user": updated.to_dict()}


@app.post("/api/admin/users/{user_id}/add-pages")
async def admin_add_pages(user_id: str, data: dict, admin: User = Depends(require_admin)):
    """Add pages to a user's balance."""
    user = get_user(user_id)
    if not user:
        return JSONResponse(status_code=404, content={"error": "User not found"})

    pages = data.get("pages")
    if not isinstance(pages, int) or pages < 1:
        return JSONResponse(status_code=400, content={"error": "pages must be a positive integer"})

    updated = add_pages(user_id, pages)
    return {"user": updated.to_dict()}


@app.get("/api/admin/stats")
async def admin_stats(admin: User = Depends(require_admin)):
    """Aggregate stats: total users, total docs processed, total pages used, users by tier."""
    users = list_users()
    total_users = len(users)
    total_docs = sum(u.documents_used for u in users)
    total_pages = sum(u.pages_used for u in users)
    by_tier: dict[str, int] = {}
    for u in users:
        by_tier[u.tier] = by_tier.get(u.tier, 0) + 1

    return {
        "total_users": total_users,
        "total_documents_processed": total_docs,
        "total_pages_used": total_pages,
        "users_by_tier": by_tier,
    }


# ── Billing endpoints ────────────────────────────────────────────

@app.post("/api/billing/packs")
async def billing_packs(_rate=Depends(_general_limit)):
    """Return available credit packs. No auth required."""
    return {"packs": get_packs_for_display()}


@app.post("/api/billing/create-checkout")
async def billing_create_checkout(data: dict, user: User = Depends(require_user), _rate=Depends(_checkout_limit)):
    """Create a Stripe Checkout Session for a credit pack purchase."""
    pack_id = data.get("pack_id", "")
    if not pack_id:
        return JSONResponse(status_code=400, content={"error": "pack_id is required"})

    from src.web.email import SITE_URL
    success_url = f"{SITE_URL}/?payment=success"
    cancel_url = f"{SITE_URL}/?payment=cancelled"

    try:
        checkout_url = create_checkout_session(user.id, pack_id, success_url, cancel_url)
        return {"url": checkout_url}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.exception("Failed to create checkout session")
        return JSONResponse(status_code=500, content={"error": "Payment system unavailable"})


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Handle Stripe webhook events. No auth — uses Stripe signature verification."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not sig_header:
        return JSONResponse(status_code=400, content={"error": "Missing signature"})

    try:
        result = handle_webhook(payload, sig_header)
        return result
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except WebhookError as e:
        # Transient error — return 500 so Stripe retries
        logger.error("Webhook processing error: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/billing/transactions")
async def billing_transactions(user: User = Depends(require_user), _rate=Depends(_general_limit)):
    """Get the authenticated user's transaction history."""
    transactions = get_user_transactions(user.id)
    return {"transactions": transactions}


# ── Background processing ───────────────────────────────────────

def _send_notification(job_id: str) -> None:
    """Send email notification for a completed/failed job."""
    try:
        job = get_job(job_id)
        if not job or not job.user_id:
            return
        user = get_user(job.user_id)
        if not user or not user.email:
            return
        if job.status == "completed":
            send_job_complete_email(user.email, job)
        elif job.status == "failed":
            send_job_failed_email(user.email, job)
    except Exception:
        logger.exception("Failed to send notification for job %s", job_id)


def _save_result_json(output_dir: Path, job_id: str, result) -> None:
    """Save the full RemediationResult as JSON for analysis and improvement."""
    try:
        result_path = output_dir / f"{job_id}_result.json"
        result_path.write_text(result.model_dump_json(indent=2))
        logger.info("Saved pipeline result to %s", result_path)
    except Exception:
        logger.exception("Failed to save result JSON for job %s", job_id)


def _process_job(job_id: str) -> None:
    """Process a remediation job in the background.

    Uses a semaphore to ensure only one job processes at a time,
    avoiding API rate limit errors when multiple docs are uploaded.
    """
    job = get_job(job_id)
    if not job:
        return

    update_job(job_id, status="queued", phase="waiting")
    _processing_semaphore.acquire()
    try:
        _process_job_inner(job_id)
    finally:
        _processing_semaphore.release()


def _process_job_inner(job_id: str) -> None:
    """Inner processing logic, called while holding the semaphore."""
    job = get_job(job_id)
    if not job:
        return

    now = datetime.now(timezone.utc).isoformat()
    update_job(job_id, status="processing", phase="", started_at=now)
    logger.info("Processing job %s: %s", job_id, job.filename)

    def on_phase(phase: str, detail: str = "") -> None:
        update_job(job_id, phase=phase, phase_detail=detail)

    try:
        # Build output dir for this job
        job_output_dir = OUTPUT_DIR / job_id
        job_output_dir.mkdir(parents=True, exist_ok=True)

        request = RemediationRequest(
            document_path=job.original_path,
            output_dir=str(job_output_dir),
            course_context=CourseContext(
                course_name=job.course_name,
                department=job.department,
            ),
        )

        result = process(request, on_phase=on_phase)

        # Save full pipeline result for analysis and improvement
        _save_result_json(job_output_dir, job_id, result)

        # Record actual API cost for cost-cap accounting (success or failure)
        try:
            record_job_cost(job_id, result.cost_summary.estimated_cost_usd)
        except Exception:
            logger.exception("Failed to record cost for job %s", job_id)

        if result.success:
            update_job(
                job_id,
                status="completed",
                phase="",
                output_path=result.output_path or "",
                report_path=result.report_path or "",
                companion_path=result.companion_output_path or "",
                issues_before=result.issues_before,
                issues_after=result.issues_after,
                issues_fixed=result.issues_fixed,
                human_review_count=len(result.items_for_human_review),
                processing_time=result.processing_time_seconds,
            )
            logger.info("Job %s completed: %d→%d issues", job_id, result.issues_before, result.issues_after)
            _send_notification(job_id)
        else:
            update_job(
                job_id,
                status="failed",
                phase="",
                error=result.error or "Unknown error",
                processing_time=result.processing_time_seconds,
            )
            # Refund pages on failure (skip for free LaTeX conversions)
            was_latex_free = Path(job.filename).suffix.lower() in (".tex", ".ltx", ".zip")
            if job.user_id and job.page_count > 0 and not was_latex_free:
                refund_pages(job.user_id, job.page_count)
                logger.info("Refunded %d pages to user %s for failed job %s", job.page_count, job.user_id, job_id)
            logger.error("Job %s failed: %s", job_id, result.error)
            _send_notification(job_id)

    except Exception as e:
        logger.exception("Job %s crashed", job_id)
        update_job(job_id, status="failed", phase="", error=str(e))
        # Refund pages on crash (skip for free LaTeX conversions)
        was_latex_free = Path(job.filename).suffix.lower() in (".tex", ".ltx", ".zip")
        if job.user_id and job.page_count > 0 and not was_latex_free:
            refund_pages(job.user_id, job.page_count)
            logger.info("Refunded %d pages to user %s for crashed job %s", job.page_count, job.user_id, job_id)
        _send_notification(job_id)
