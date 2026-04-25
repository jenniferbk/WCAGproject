"""Per-user job caps: concurrent in-flight + hourly submission rate.

Enforced at upload time, on top of the existing per-IP rate limit and the
per-user pages_balance. Defends the queue against a single user
monopolizing capacity during a surge (e.g., one faculty member uploading
a large batch all at once during a pilot).

Independent from src/web/cost_cap.py (system-wide spend) and from the
existing _upload_limit (per-IP rate limit). All three checks run in turn.

Configuration (read at check time so changes take effect without restart):
- MAX_USER_CONCURRENT_JOBS: max in-flight jobs per user (queued + processing).
  Empty/0/unset = unlimited. Default 5.
- MAX_USER_JOBS_PER_HOUR: max jobs created in the trailing 60 minutes per user.
  Empty/0/unset = unlimited. Default 30.

Admin users are exempt from both caps.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.web.jobs import _get_conn

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENT = 5
DEFAULT_HOURLY = 30


@dataclass(frozen=True)
class UserCapStatus:
    allowed: bool
    reason: str             # "ok", "concurrent_cap", "hourly_cap"
    concurrent_jobs: int
    hourly_jobs: int
    concurrent_cap: int | None
    hourly_cap: int | None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "concurrent_jobs": self.concurrent_jobs,
            "hourly_jobs": self.hourly_jobs,
            "concurrent_cap": self.concurrent_cap,
            "hourly_cap": self.hourly_cap,
        }


def _parse_cap(name: str, default: int) -> int | None:
    """Parse a cap from env. Empty/missing → default. 0 / negative / invalid → unlimited (None)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        logger.warning("Invalid int in env %s=%r — treating as unlimited", name, raw)
        return None
    return n if n > 0 else None


def count_active_jobs(user_id: str) -> int:
    """Count jobs in 'queued' or 'processing' state for a user."""
    if not user_id:
        return 0
    conn = _get_conn()
    row = conn.execute(
        """SELECT COUNT(*) FROM jobs
           WHERE user_id = ? AND status IN ('queued', 'processing')""",
        (user_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def count_recent_jobs(user_id: str, hours: int = 1) -> int:
    """Count jobs created by user in the trailing N hours."""
    if not user_id:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE user_id = ? AND created_at >= ?",
        (user_id, cutoff),
    ).fetchone()
    return int(row[0]) if row else 0


def check_user_caps(user_id: str, is_admin: bool = False) -> UserCapStatus:
    """Decide whether this user can submit another job.

    Admins bypass caps. Returned status carries current counts for telemetry.
    """
    concurrent_cap = _parse_cap("MAX_USER_CONCURRENT_JOBS", DEFAULT_CONCURRENT)
    hourly_cap = _parse_cap("MAX_USER_JOBS_PER_HOUR", DEFAULT_HOURLY)

    if is_admin:
        return UserCapStatus(
            allowed=True, reason="ok",
            concurrent_jobs=0, hourly_jobs=0,
            concurrent_cap=concurrent_cap, hourly_cap=hourly_cap,
        )

    concurrent = count_active_jobs(user_id)
    hourly = count_recent_jobs(user_id, hours=1)

    if concurrent_cap is not None and concurrent >= concurrent_cap:
        logger.warning(
            "User %s blocked by concurrent cap (%d in flight, cap=%d)",
            user_id, concurrent, concurrent_cap,
        )
        return UserCapStatus(
            allowed=False, reason="concurrent_cap",
            concurrent_jobs=concurrent, hourly_jobs=hourly,
            concurrent_cap=concurrent_cap, hourly_cap=hourly_cap,
        )
    if hourly_cap is not None and hourly >= hourly_cap:
        logger.warning(
            "User %s blocked by hourly cap (%d in last hour, cap=%d)",
            user_id, hourly, hourly_cap,
        )
        return UserCapStatus(
            allowed=False, reason="hourly_cap",
            concurrent_jobs=concurrent, hourly_jobs=hourly,
            concurrent_cap=concurrent_cap, hourly_cap=hourly_cap,
        )

    return UserCapStatus(
        allowed=True, reason="ok",
        concurrent_jobs=concurrent, hourly_jobs=hourly,
        concurrent_cap=concurrent_cap, hourly_cap=hourly_cap,
    )
