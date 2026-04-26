"""Run the ARQ worker.

Usage:
    QUEUE_BACKEND=arq REDIS_URL=redis://localhost:6379 python -m scripts.run_arq_worker

Concurrency is controlled by `MAX_CONCURRENT_JOBS` (same env var as the
threading path). Stop with SIGTERM; ARQ drains in-flight jobs before
exiting (up to job_timeout = 30 minutes).

For systemd: see deployment notes in CLAUDE.md.
"""

from __future__ import annotations

import logging
import sys

from arq.worker import run_worker

from src.web.observability import configure_logging
from src.web.queue import WorkerSettings


def main() -> int:
    configure_logging(level=logging.INFO)
    log = logging.getLogger(__name__)
    log.info(
        "Starting ARQ worker (max_jobs=%d, redis=%s)",
        WorkerSettings.max_jobs,
        WorkerSettings.redis_settings().dsn if hasattr(WorkerSettings.redis_settings(), "dsn") else "(see env)",
    )
    run_worker(WorkerSettings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
