"""Job tracking with SQLite.

Stores upload/processing state so users can check status and
retrieve results across page loads.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "jobs.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn


def init_db() -> None:
    """Create the jobs table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            original_path TEXT NOT NULL,
            output_path TEXT DEFAULT '',
            report_path TEXT DEFAULT '',
            status TEXT DEFAULT 'queued',
            issues_before INTEGER DEFAULT 0,
            issues_after INTEGER DEFAULT 0,
            issues_fixed INTEGER DEFAULT 0,
            human_review_count INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            course_name TEXT DEFAULT '',
            department TEXT DEFAULT '',
            processing_time REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


@dataclass
class Job:
    id: str
    filename: str
    original_path: str
    output_path: str
    report_path: str
    status: str
    issues_before: int
    issues_after: int
    issues_fixed: int
    human_review_count: int
    error: str
    course_name: str
    department: str
    processing_time: float
    created_at: str
    updated_at: str
    user_id: str = ""
    batch_id: str = ""
    phase: str = ""
    companion_path: str = ""

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "issues_before": self.issues_before,
            "issues_after": self.issues_after,
            "issues_fixed": self.issues_fixed,
            "human_review_count": self.human_review_count,
            "error": self.error,
            "course_name": self.course_name,
            "department": self.department,
            "processing_time": round(self.processing_time, 1),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "batch_id": self.batch_id,
            "phase": self.phase,
        }
        if self.companion_path:
            d["has_companion"] = True
        return d


def _row_to_job(row: sqlite3.Row) -> Job:
    d = dict(row)
    # Columns may not exist in old databases before migration
    d.setdefault("user_id", "")
    d.setdefault("batch_id", "")
    d.setdefault("phase", "")
    d.setdefault("companion_path", "")
    return Job(**d)


def create_job(
    filename: str,
    original_path: str,
    course_name: str = "",
    department: str = "",
    user_id: str = "",
    batch_id: str = "",
) -> Job:
    """Create a new job record."""
    conn = _get_conn()
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO jobs (id, filename, original_path, course_name, department, user_id, batch_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, filename, original_path, course_name, department, user_id, batch_id, now, now),
    )
    conn.commit()
    return get_job(job_id)


def get_job(job_id: str) -> Job | None:
    """Get a job by ID."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 50, user_id: str = "") -> list[Job]:
    """List recent jobs, newest first. Filter by user_id if provided."""
    conn = _get_conn()
    if user_id:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def list_jobs_by_batch(batch_id: str, user_id: str = "") -> list[Job]:
    """List jobs in a batch, newest first. Filter by user_id if provided."""
    conn = _get_conn()
    if user_id:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE batch_id = ? AND user_id = ? ORDER BY created_at DESC",
            (batch_id, user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at DESC",
            (batch_id,),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def update_job(job_id: str, **kwargs) -> Job | None:
    """Update job fields."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    kwargs["updated_at"] = now

    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", values)
    conn.commit()
    return get_job(job_id)


def delete_job(job_id: str) -> bool:
    """Delete a single job record. Returns True if a row was deleted."""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    return cursor.rowcount > 0


def delete_jobs(job_ids: list[str], user_id: str) -> int:
    """Bulk delete jobs owned by user_id, skipping queued/processing. Returns count deleted."""
    if not job_ids:
        return 0
    conn = _get_conn()
    placeholders = ",".join("?" for _ in job_ids)
    cursor = conn.execute(
        f"DELETE FROM jobs WHERE id IN ({placeholders}) AND user_id = ? AND status NOT IN ('queued', 'processing')",
        [*job_ids, user_id],
    )
    conn.commit()
    return cursor.rowcount


def get_deletable_jobs(job_ids: list[str], user_id: str) -> list[Job]:
    """Fetch jobs that can be deleted (owned by user, not queued/processing)."""
    if not job_ids:
        return []
    conn = _get_conn()
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({placeholders}) AND user_id = ? AND status NOT IN ('queued', 'processing')",
        [*job_ids, user_id],
    ).fetchall()
    return [_row_to_job(row) for row in rows]


def get_jobs_by_ids(job_ids: list[str], user_id: str) -> list[Job]:
    """Fetch multiple jobs by ID, filtered by ownership."""
    if not job_ids:
        return []
    conn = _get_conn()
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({placeholders}) AND user_id = ?",
        [*job_ids, user_id],
    ).fetchall()
    return [_row_to_job(row) for row in rows]
