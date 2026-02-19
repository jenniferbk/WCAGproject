"""FastAPI web application for document accessibility remediation.

Provides:
- User registration and authentication (local + OAuth)
- File upload with per-user usage limits
- Job status tracking (per-user isolation)
- Report viewing and remediated file download
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest
from src.web.auth import (
    clear_session_cookie,
    create_token,
    hash_password,
    set_session_cookie,
    verify_password,
)
from src.web.jobs import create_job, get_job, init_db, list_jobs, update_job
from src.web.middleware import get_current_user, require_user
from src.web.users import (
    User,
    create_user,
    get_user_by_email,
    get_user_by_oauth,
    increment_documents_used,
    init_users_db,
)

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "output"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="A11y Remediation", version="0.1.0")


@app.on_event("startup")
def startup():
    init_db()
    init_users_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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
        token = create_token(user.id, user.email)
        response = HTMLResponse('<script>window.location="/"</script>')
        set_session_cookie(response, token)
        return response
    except Exception as e:
        logger.exception("Microsoft OAuth callback failed")
        return HTMLResponse(f'<script>window.location="/?error=oauth_failed"</script>')


# ── API endpoints (protected) ───────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    user: User = Depends(require_user),
    file: UploadFile = File(...),
    course_name: str = Form(""),
    department: str = Form(""),
):
    """Upload a document for remediation. Requires authentication."""
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in (".docx", ".pdf", ".pptx"):
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: {suffix}. Accepts .docx, .pdf, .pptx"},
        )

    # Check usage limits
    if user.documents_used >= user.max_documents:
        return JSONResponse(
            status_code=403,
            content={
                "error": "Document limit reached",
                "documents_used": user.documents_used,
                "max_documents": user.max_documents,
            },
        )

    # Read and check file size
    content = await file.read()
    max_bytes = user.max_file_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        return JSONResponse(
            status_code=413,
            content={"error": f"File too large. Maximum size is {user.max_file_size_mb}MB"},
        )

    # Atomically increment usage
    if not increment_documents_used(user.id):
        return JSONResponse(
            status_code=403,
            content={"error": "Document limit reached"},
        )

    # Save uploaded file
    job = create_job(filename, "", course_name, department, user_id=user.id)
    upload_path = UPLOAD_DIR / f"{job.id}_{filename}"

    with open(upload_path, "wb") as f:
        f.write(content)

    update_job(job.id, original_path=str(upload_path), status="queued")

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


# ── Background processing ───────────────────────────────────────

def _process_job(job_id: str) -> None:
    """Process a remediation job in the background."""
    job = get_job(job_id)
    if not job:
        return

    update_job(job_id, status="processing")
    logger.info("Processing job %s: %s", job_id, job.filename)

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

        result = process(request)

        if result.success:
            update_job(
                job_id,
                status="completed",
                output_path=result.output_path or "",
                report_path=result.report_path or "",
                issues_before=result.issues_before,
                issues_after=result.issues_after,
                issues_fixed=result.issues_fixed,
                human_review_count=len(result.items_for_human_review),
                processing_time=result.processing_time_seconds,
            )
            logger.info("Job %s completed: %d→%d issues", job_id, result.issues_before, result.issues_after)
        else:
            update_job(
                job_id,
                status="failed",
                error=result.error or "Unknown error",
                processing_time=result.processing_time_seconds,
            )
            logger.error("Job %s failed: %s", job_id, result.error)

    except Exception as e:
        logger.exception("Job %s crashed", job_id)
        update_job(job_id, status="failed", error=str(e))
