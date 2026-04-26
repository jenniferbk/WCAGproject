"""ARQ + Redis job queue (production) layered on top of the existing
threading-based path (default).

Selection is by env var:
- `QUEUE_BACKEND=arq` (or `=redis`): enqueue jobs to ARQ; workers run
  in a separate process via `python -m src.web.queue_worker`.
- Anything else (default): use the historical `threading.Thread`
  daemons started directly from the upload endpoint. Backward
  compatible — no code change required to keep current behavior.

Why the optional gating:
- ARQ requires Redis; we don't want a soft dependency on Redis to
  break dev workflows where Redis isn't installed.
- The threading path is already used in production today; flipping the
  switch is one env var change, not a redeploy.

Tests mock `enqueue_job` so they don't need Redis at unit-test time.
A live integration test in tests/test_arq_smoke.py runs against the
local Redis if `REDIS_URL` is set.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_backend() -> str:
    """Return 'arq' or 'thread'. Default 'thread' for backward compat."""
    raw = os.environ.get("QUEUE_BACKEND", "").strip().lower()
    return "arq" if raw in ("arq", "redis") else "thread"


def get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379")


# ── ARQ enqueue path ─────────────────────────────────────────────

async def _enqueue_async(job_id: str) -> None:
    """Push a job to ARQ via async client. Imports inside to keep arq
    optional — no install required for the threading path."""
    from arq import create_pool
    from arq.connections import RedisSettings

    settings = RedisSettings.from_dsn(get_redis_url())
    pool = await create_pool(settings)
    try:
        # `_job_id` is ARQ's deduplication key — same job_id won't enqueue
        # twice. Use our app's job UUID so retries can be idempotent.
        await pool.enqueue_job("process_job_task", job_id, _job_id=f"a11y:{job_id}")
        logger.info("Enqueued job %s to ARQ", job_id)
    finally:
        await pool.close()


def enqueue_job(job_id: str) -> None:
    """Sync wrapper around the async enqueue. Safe to call from FastAPI
    sync handlers. Falls back to threading if ARQ isn't selected."""
    if get_backend() != "arq":
        # Not configured for ARQ — caller should use threading directly.
        # We still expose this function so the caller doesn't need to
        # branch on backend; but we make the misuse explicit.
        raise RuntimeError(
            "enqueue_job called but QUEUE_BACKEND is not 'arq'. "
            "Caller should branch on get_backend() or use threading directly."
        )
    asyncio.run(_enqueue_async(job_id))


# ── ARQ worker function ──────────────────────────────────────────

async def process_job_task(ctx: dict, job_id: str) -> None:
    """ARQ worker entry: hands job off to the existing _process_job_inner.

    Runs the sync pipeline in a thread executor so the ARQ event loop
    stays responsive (the orchestrator pipeline is CPU-heavy and calls
    blocking subprocess + HTTP libraries internally).
    """
    from src.web.app import _process_job_inner

    logger.info("ARQ worker picking up job %s", job_id)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _process_job_inner, job_id)
    except Exception:
        logger.exception("ARQ worker crashed processing job %s", job_id)
        # Re-raise so ARQ marks the job failed; user-visible job state
        # is already set to 'failed' inside _process_job_inner's except.
        raise
    logger.info("ARQ worker finished job %s", job_id)


# ── ARQ WorkerSettings (loaded by `arq` CLI / queue_worker entrypoint) ─

class WorkerSettings:
    """ARQ uses class attributes here as worker config."""

    functions = [process_job_task]

    # Concurrency: how many concurrent jobs per worker process. Match
    # MAX_CONCURRENT_JOBS for parity with the threading path.
    @classmethod
    def _max_jobs(cls) -> int:
        raw = os.environ.get("MAX_CONCURRENT_JOBS", "").strip()
        try:
            return max(1, int(raw)) if raw else 1
        except ValueError:
            return 1

    max_jobs = 1  # set dynamically below

    @classmethod
    def redis_settings(cls):
        from arq.connections import RedisSettings
        return RedisSettings.from_dsn(get_redis_url())

    # Job timeout: long enough for any single doc, but bounded so a
    # crashed pipeline doesn't pin a worker forever. 30 minutes is
    # generous given current ~2.5 min/doc avg.
    job_timeout = 30 * 60

    # Retry: don't loop on permanent failures. The pipeline already
    # writes 'failed' status on errors; ARQ shouldn't pick it up again.
    max_tries = 1


# Initialize max_jobs from env at module import (workers re-import this
# module on startup so the env var is honored).
WorkerSettings.max_jobs = WorkerSettings._max_jobs()
