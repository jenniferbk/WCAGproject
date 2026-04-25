"""Storage retention: delete old uploads and outputs on a schedule.

Runs as a background thread on app startup. Iterates the upload + output
directories, deletes files older than the configured window, and logs each
deletion for audit.

Job records (in SQLite) are NOT deleted — they retain history for cost
tracking, usage analytics, and audit. Only the on-disk files are removed.
After cleanup, the job's original_path/output_path columns will reference
nonexistent files and the corresponding download endpoints will 404 cleanly.

Configuration:
- RETENTION_ENABLED: "0"/"false"/"no" disables cleanup entirely. Default enabled.
- RETENTION_DAYS_UPLOADS: age in days for data/uploads/ deletion. Default 30.
- RETENTION_DAYS_OUTPUT: age in days for data/output/ deletion. Default 30.
- RETENTION_INTERVAL_HOURS: how often the cleanup loop runs. Default 24.

Active jobs (queued / processing) are skipped — we never delete files
referenced by an in-flight job. The skip is keyed off jobs.original_path
and jobs.output_path columns.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.web.jobs import _get_conn

logger = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    """Summary of one cleanup run. Returned for tests + admin endpoint."""
    files_scanned: int = 0
    files_deleted: int = 0
    bytes_freed: int = 0
    files_skipped_active: int = 0
    files_skipped_recent: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_deleted": self.files_deleted,
            "bytes_freed": self.bytes_freed,
            "files_skipped_active": self.files_skipped_active,
            "files_skipped_recent": self.files_skipped_recent,
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _is_enabled() -> bool:
    raw = os.environ.get("RETENTION_ENABLED", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _retention_days(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
        return n if n > 0 else default
    except ValueError:
        logger.warning("Invalid int in env %s=%r — using default %d", name, raw, default)
        return default


def _interval_seconds() -> int:
    raw = os.environ.get("RETENTION_INTERVAL_HOURS", "").strip()
    try:
        hours = int(raw) if raw else 24
        if hours < 1:
            hours = 24
    except ValueError:
        hours = 24
    return hours * 3600


def _active_paths() -> set[str]:
    """Paths referenced by jobs in queued/processing state — never delete these."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT original_path, output_path FROM jobs
           WHERE status IN ('queued', 'processing')"""
    ).fetchall()
    paths: set[str] = set()
    for row in rows:
        if row[0]:
            paths.add(str(Path(row[0]).resolve()))
        if row[1]:
            paths.add(str(Path(row[1]).resolve()))
    return paths


def _delete_old_files_in(
    directory: Path,
    cutoff_seconds: float,
    active_paths: set[str],
    report: CleanupReport,
) -> None:
    """Delete files (and empty subdirs) older than the cutoff. Logs each deletion."""
    if not directory.exists():
        return

    for path in sorted(directory.rglob("*"), reverse=True):  # files before dirs
        if path.is_dir():
            # Remove if empty and old
            try:
                if not any(path.iterdir()) and path.stat().st_mtime < cutoff_seconds:
                    path.rmdir()
            except OSError:
                pass
            continue

        if not path.is_file():
            continue

        report.files_scanned += 1
        try:
            stat = path.stat()
        except OSError as e:
            report.errors.append(f"stat {path}: {e}")
            continue

        if stat.st_mtime >= cutoff_seconds:
            report.files_skipped_recent += 1
            continue

        resolved = str(path.resolve())
        if resolved in active_paths:
            report.files_skipped_active += 1
            logger.info("Retention: skipping active-job file %s", path)
            continue

        try:
            size = stat.st_size
            path.unlink()
            report.files_deleted += 1
            report.bytes_freed += size
            logger.info("Retention: deleted %s (%d bytes, age=%.1fd)",
                        path, size, (time.time() - stat.st_mtime) / 86400)
        except OSError as e:
            report.errors.append(f"unlink {path}: {e}")


def run_cleanup(upload_dir: Path, output_dir: Path) -> CleanupReport:
    """Single-shot cleanup pass. Idempotent; safe to call manually."""
    report = CleanupReport(started_at=datetime.now(timezone.utc).isoformat())

    if not _is_enabled():
        logger.info("Retention disabled via RETENTION_ENABLED")
        report.finished_at = datetime.now(timezone.utc).isoformat()
        return report

    upload_days = _retention_days("RETENTION_DAYS_UPLOADS", 30)
    output_days = _retention_days("RETENTION_DAYS_OUTPUT", 30)
    now = time.time()
    upload_cutoff = now - (upload_days * 86400)
    output_cutoff = now - (output_days * 86400)

    active = _active_paths()
    logger.info(
        "Retention cleanup: uploads>%dd output>%dd, %d active paths skipped",
        upload_days, output_days, len(active),
    )

    _delete_old_files_in(upload_dir, upload_cutoff, active, report)
    _delete_old_files_in(output_dir, output_cutoff, active, report)

    report.finished_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Retention cleanup done: scanned=%d deleted=%d bytes_freed=%d skipped_active=%d errors=%d",
        report.files_scanned, report.files_deleted, report.bytes_freed,
        report.files_skipped_active, len(report.errors),
    )
    return report


def start_background_loop(upload_dir: Path, output_dir: Path) -> threading.Thread | None:
    """Start a daemon thread that runs cleanup every RETENTION_INTERVAL_HOURS."""
    if not _is_enabled():
        logger.info("Retention loop not started (RETENTION_ENABLED disables)")
        return None

    interval = _interval_seconds()

    def _loop():
        # Sleep first so we don't fire immediately at every restart
        time.sleep(60)
        while True:
            try:
                run_cleanup(upload_dir, output_dir)
            except Exception:
                logger.exception("Retention cleanup crashed")
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="retention-cleanup")
    t.start()
    logger.info("Retention loop started (interval=%ds)", interval)
    return t
