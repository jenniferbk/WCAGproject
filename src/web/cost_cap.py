"""Global cost cap with kill switch.

Tracks cumulative API spend against env-configured ceilings and rejects new
jobs when the cap is hit. Independent of per-user `pages_balance`; this is a
system-wide fail-safe against runaway spend on the shared API keys.

Configuration (read from env at check time, so changes take effect without restart):
- COST_CAP_DAILY_USD: float ceiling for today's API spend. Empty/0/unset = unlimited.
- COST_CAP_WEEKLY_USD: float ceiling for the trailing 7 days. Empty/0/unset = unlimited.
- COST_CAP_KILL_SWITCH: "1"/"true"/"yes" to reject all new jobs immediately.

Spend is read from the `jobs.estimated_cost_usd` column, populated by the
orchestrator's CostSummary after each completed job. In-flight jobs are not
counted (no good estimate before completion).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.web.jobs import _get_conn

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CostStatus:
    """Snapshot of current spend against caps. Returned by check functions."""
    allowed: bool
    reason: str           # "ok", "kill_switch", "daily_cap_exceeded", "weekly_cap_exceeded"
    daily_cost_usd: float
    weekly_cost_usd: float
    daily_cap_usd: float | None
    weekly_cap_usd: float | None
    kill_switch: bool

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "daily_cost_usd": round(self.daily_cost_usd, 4),
            "weekly_cost_usd": round(self.weekly_cost_usd, 4),
            "daily_cap_usd": self.daily_cap_usd,
            "weekly_cap_usd": self.weekly_cap_usd,
            "kill_switch": self.kill_switch,
        }


def _parse_float_env(name: str) -> float | None:
    """Parse an optional float env var. Empty/missing/0/invalid returns None (= unlimited)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        logger.warning("Invalid float in env %s=%r — treating as unlimited", name, raw)
        return None
    return v if v > 0 else None


def _kill_switch_active() -> bool:
    return os.environ.get("COST_CAP_KILL_SWITCH", "").strip().lower() in ("1", "true", "yes", "on")


def _sum_cost_since(cutoff_iso: str) -> float:
    """Sum estimated_cost_usd for jobs created on or after cutoff_iso."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM jobs WHERE created_at >= ?",
        (cutoff_iso,),
    ).fetchone()
    return float(row[0]) if row else 0.0


def current_status() -> CostStatus:
    """Read current spend windows and decide whether new jobs are allowed."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()

    daily_cost = _sum_cost_since(day_start)
    weekly_cost = _sum_cost_since(week_start)
    daily_cap = _parse_float_env("COST_CAP_DAILY_USD")
    weekly_cap = _parse_float_env("COST_CAP_WEEKLY_USD")
    kill_switch = _kill_switch_active()

    if kill_switch:
        reason = "kill_switch"
        allowed = False
    elif daily_cap is not None and daily_cost >= daily_cap:
        reason = "daily_cap_exceeded"
        allowed = False
    elif weekly_cap is not None and weekly_cost >= weekly_cap:
        reason = "weekly_cap_exceeded"
        allowed = False
    else:
        reason = "ok"
        allowed = True

    return CostStatus(
        allowed=allowed,
        reason=reason,
        daily_cost_usd=daily_cost,
        weekly_cost_usd=weekly_cost,
        daily_cap_usd=daily_cap,
        weekly_cap_usd=weekly_cap,
        kill_switch=kill_switch,
    )


def check_can_submit() -> CostStatus:
    """Pre-flight check before accepting a new job. Logs rejection reason."""
    status = current_status()
    if not status.allowed:
        logger.warning(
            "Cost cap rejecting job — reason=%s daily=$%.4f/%.2f weekly=$%.4f/%.2f kill_switch=%s",
            status.reason,
            status.daily_cost_usd,
            status.daily_cap_usd or 0,
            status.weekly_cost_usd,
            status.weekly_cap_usd or 0,
            status.kill_switch,
        )
    return status


def record_job_cost(job_id: str, cost_usd: float) -> None:
    """Persist the actual cost of a completed job to jobs.estimated_cost_usd."""
    conn = _get_conn()
    conn.execute(
        "UPDATE jobs SET estimated_cost_usd = ? WHERE id = ?",
        (round(cost_usd, 6), job_id),
    )
    conn.commit()


def ensure_cost_column() -> None:
    """Idempotent migration: add estimated_cost_usd column to jobs if missing."""
    conn = _get_conn()
    cursor = conn.execute("PRAGMA table_info(jobs)")
    columns = {row[1] for row in cursor.fetchall()}
    if "estimated_cost_usd" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN estimated_cost_usd REAL DEFAULT 0")
        conn.commit()
        logger.info("Added jobs.estimated_cost_usd column")
