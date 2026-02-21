"""FastAPI web application for document accessibility remediation.

Provides:
- User registration and authentication (local + OAuth)
- File upload with per-user usage limits
- Job status tracking (per-user isolation)
- Report viewing and remediated file download
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import io
import zipfile

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest
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

UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "output"
STATIC_DIR = Path(__file__).parent / "static"

# Limit concurrent remediation jobs to avoid API rate limits.
# With a 30k input tokens/min Claude rate limit, only one job
# can safely process at a time.
_processing_semaphore = threading.Semaphore(1)

app = FastAPI(title="A11y Remediation", version="0.1.0")


@app.on_event("startup")
def startup():
    init_db()
    init_users_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _check_admin_promotion(user: User) -> User:
    """Auto-promote user to admin if their email is in ADMIN_EMAILS."""
    admin_emails_raw = os.environ.get("ADMIN_EMAILS", "jennifer.b.kleiman@gmail.com")
    admin_emails = {e.strip().lower() for e in admin_emails_raw.split(",") if e.strip()}
    if user.email.lower() in admin_emails and not user.is_admin:
        updated = update_user(user.id, is_admin=True)
        if updated:
            return updated
    return user


@app.get("/api/health")
async def health():
    """Health check endpoint for deployment verification."""
    return {"status": "ok"}


# ── Static frontend ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text())


# ── Auth endpoints ───────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(data: dict):
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

    hashed = hash_password(password)
    user = create_user(email=email, password_hash=hashed, display_name=display_name or email.split("@")[0])
    user = _check_admin_promotion(user)

    token = create_token(user.id, user.email)
    response = JSONResponse(content={"user": user.to_dict()})
    set_session_cookie(response, token)
    return response


@app.post("/api/auth/login")
async def login(data: dict):
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
async def logout():
    """Clear the session cookie."""
    response = JSONResponse(content={"ok": True})
    clear_session_cookie(response)
    return response


@app.get("/api/auth/me")
async def me(user: User | None = Depends(get_current_user)):
    """Return current user info, or 401 if not authenticated."""
    if not user:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    return {"user": user.to_dict()}


@app.post("/api/auth/forgot-password")
async def forgot_password(data: dict):
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
async def reset_password(data: dict):
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
):
    """Upload a document for remediation. Requires authentication."""
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in (".docx", ".pdf", ".pptx"):
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: {suffix}. Accepts .docx, .pdf, .pptx"},
        )

    # Read and check file size
    content = await file.read()
    max_bytes = user.max_file_size_mb * 1024 * 1024
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

    # Atomically deduct pages from balance
    if not deduct_pages(user.id, page_count):
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

    # Start processing in background
    thread = threading.Thread(
        target=_process_job,
        args=(job.id,),
        daemon=True,
    )
    thread.start()

    return {"job_id": job.id, "status": "queued", "filename": filename}


@app.get("/api/jobs")
async def get_jobs(user: User = Depends(require_user)):
    """List jobs for the authenticated user."""
    jobs = list_jobs(user_id=user.id)
    return {"jobs": [j.to_dict() for j in jobs]}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str, user: User = Depends(require_user)):
    """Get status of a specific job. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return job.to_dict()


@app.get("/api/jobs/{job_id}/report")
async def get_report(job_id: str, user: User = Depends(require_user)):
    """Get the HTML compliance report. Requires ownership."""
    job = get_job(job_id)
    if not job or job.user_id != user.id:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.report_path or not Path(job.report_path).exists():
        return JSONResponse(status_code=404, content={"error": "Report not available"})
    return HTMLResponse(Path(job.report_path).read_text())


@app.get("/api/jobs/{job_id}/download")
async def download_file(job_id: str, user: User = Depends(require_user)):
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
async def download_original(job_id: str, user: User = Depends(require_user)):
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
async def download_accessible(job_id: str, user: User = Depends(require_user)):
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
async def delete_single_job(job_id: str, user: User = Depends(require_user)):
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
async def bulk_delete_jobs(data: dict, user: User = Depends(require_user)):
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
async def download_zip(data: dict, user: User = Depends(require_user)):
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
async def get_batch(batch_id: str, user: User = Depends(require_user)):
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

    update_job(job_id, status="processing", phase="")
    logger.info("Processing job %s: %s", job_id, job.filename)

    def on_phase(phase: str) -> None:
        update_job(job_id, phase=phase)

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
            # Refund pages on failure
            if job.user_id and job.page_count > 0:
                refund_pages(job.user_id, job.page_count)
                logger.info("Refunded %d pages to user %s for failed job %s", job.page_count, job.user_id, job_id)
            logger.error("Job %s failed: %s", job_id, result.error)
            _send_notification(job_id)

    except Exception as e:
        logger.exception("Job %s crashed", job_id)
        update_job(job_id, status="failed", phase="", error=str(e))
        # Refund pages on crash
        if job.user_id and job.page_count > 0:
            refund_pages(job.user_id, job.page_count)
            logger.info("Refunded %d pages to user %s for crashed job %s", job.page_count, job.user_id, job_id)
        _send_notification(job_id)
