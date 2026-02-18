"""FastAPI web application for document accessibility remediation.

Provides:
- File upload endpoint
- Job status tracking
- Report viewing
- Remediated file download
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest
from src.web.jobs import create_job, get_job, init_db, list_jobs, update_job

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "output"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="A11y Remediation", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Static frontend ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text())


# ── API endpoints ────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    course_name: str = Form(""),
    department: str = Form(""),
):
    """Upload a document for remediation."""
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in (".docx", ".pdf", ".pptx"):
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported file type: {suffix}. Accepts .docx, .pdf, .pptx"},
        )

    # Save uploaded file
    job = create_job(filename, "", course_name, department)
    upload_path = UPLOAD_DIR / f"{job.id}_{filename}"

    with open(upload_path, "wb") as f:
        content = await file.read()
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
async def get_jobs():
    """List all jobs."""
    jobs = list_jobs()
    return {"jobs": [j.to_dict() for j in jobs]}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a specific job."""
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return job.to_dict()


@app.get("/api/jobs/{job_id}/report")
async def get_report(job_id: str):
    """Get the HTML compliance report."""
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.report_path or not Path(job.report_path).exists():
        return JSONResponse(status_code=404, content={"error": "Report not available"})
    return HTMLResponse(Path(job.report_path).read_text())


@app.get("/api/jobs/{job_id}/download")
async def download_file(job_id: str):
    """Download the remediated document."""
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    if not job.output_path or not Path(job.output_path).exists():
        return JSONResponse(status_code=404, content={"error": "File not available"})

    return FileResponse(
        job.output_path,
        filename=Path(job.output_path).name,
        media_type="application/octet-stream",
    )


@app.get("/api/jobs/{job_id}/download-original")
async def download_original(job_id: str):
    """Download the original uploaded document."""
    job = get_job(job_id)
    if not job:
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
