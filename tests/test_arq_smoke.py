"""ARQ + Redis live smoke test.

Skipped unless `REDIS_URL` is set AND the Redis server is reachable.
Verifies that:
- An app job, dispatched via the ARQ path, lands in Redis under our
  expected job-key prefix.
- The worker function (when imported) is callable as ARQ expects.

Real worker execution (start a worker, poll job state to terminal) is
outside the scope of this smoke test — that needs an out-of-process
worker and is part of staging validation, not unit testing.
"""

from __future__ import annotations

import asyncio
import os

import pytest

REDIS_URL = os.environ.get("REDIS_URL", "")


def _redis_reachable() -> bool:
    if not REDIS_URL:
        return False
    try:
        import redis  # type: ignore
        r = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1)
        return r.ping() is True
    except Exception:
        return False


if not _redis_reachable():
    pytest.skip(
        "REDIS_URL is unset or Redis isn't reachable. Run with "
        "`REDIS_URL=redis://localhost:6379 pytest tests/test_arq_smoke.py` "
        "to enable.",
        allow_module_level=True,
    )

try:
    from arq import create_pool
    from arq.connections import RedisSettings
except ImportError:
    pytest.skip("arq not installed", allow_module_level=True)


@pytest.fixture(autouse=True)
def use_arq_backend(monkeypatch):
    monkeypatch.setenv("QUEUE_BACKEND", "arq")


@pytest.fixture(autouse=True)
def clean_redis():
    """Best-effort: flush our test queue keys before + after each test."""
    import redis

    def _flush():
        r = redis.Redis.from_url(REDIS_URL)
        # Only delete our job markers, not someone else's keys
        for key in r.scan_iter("arq:job:a11y:*"):
            r.delete(key)
        for key in r.scan_iter("arq:queue:*"):
            r.delete(key)
        for key in r.scan_iter("arq:result:a11y:*"):
            r.delete(key)
        for key in r.scan_iter("arq:in-progress:a11y:*"):
            r.delete(key)

    _flush()
    yield
    _flush()


class TestArqEnqueue:
    def test_enqueue_writes_to_redis(self):
        from src.web.queue import enqueue_job

        enqueue_job("smoke-test-job-001")

        # Verify the job exists in Redis under our prefix
        import redis
        r = redis.Redis.from_url(REDIS_URL)
        keys = list(r.scan_iter("arq:job:a11y:smoke-test-job-001"))
        assert len(keys) == 1, f"Expected ARQ job key in Redis, got: {keys}"

    def test_dedup_via_job_id(self):
        """Enqueueing the same job_id twice should produce only one queued task."""
        from src.web.queue import enqueue_job

        enqueue_job("smoke-test-dedup")
        enqueue_job("smoke-test-dedup")

        import redis
        r = redis.Redis.from_url(REDIS_URL)
        keys = list(r.scan_iter("arq:job:a11y:smoke-test-dedup"))
        # ARQ stores one job dict per dedup key
        assert len(keys) == 1


class TestWorkerSettings:
    def test_worker_settings_carries_function(self):
        from src.web.queue import WorkerSettings, process_job_task
        assert process_job_task in WorkerSettings.functions

    def test_redis_settings_callable(self):
        from src.web.queue import WorkerSettings
        # Should not raise — uses REDIS_URL env or default
        settings = WorkerSettings.redis_settings()
        assert settings is not None


class TestProcessJobTaskCallable:
    """The worker function is async and runs the sync pipeline in an
    executor. Verify it accepts the ARQ ctx + job_id signature without
    actually running the pipeline."""

    def test_signature(self):
        from src.web.queue import process_job_task
        import inspect
        sig = inspect.signature(process_job_task)
        assert list(sig.parameters.keys()) == ["ctx", "job_id"]
