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

    def to_dict(self) -> dict:
        return {
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
        }


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(**dict(row))


def create_job(filename: str, original_path: str, course_name: str = "", department: str = "") -> Job:
    """Create a new job record."""
    conn = _get_conn()
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO jobs (id, filename, original_path, course_name, department, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (job_id, filename, original_path, course_name, department, now, now),
    )
    conn.commit()
    return get_job(job_id)


def get_job(job_id: str) -> Job | None:
    """Get a job by ID."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 50) -> list[Job]:
    """List recent jobs, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
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
