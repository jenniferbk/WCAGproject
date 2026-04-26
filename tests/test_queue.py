"""Unit tests for the queue backend selector + ARQ enqueue plumbing.

These tests don't need a running Redis — they verify backend selection,
sync/async wrapping, and that the upload path uses _dispatch_job() as
the routing seam. Live ARQ + Redis integration is covered by
tests/test_arq_smoke.py (gated on REDIS_URL).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.web.queue import enqueue_job, get_backend, get_redis_url


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("QUEUE_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)


class TestGetBackend:
    def test_default_is_thread(self):
        assert get_backend() == "thread"

    def test_arq_value(self, monkeypatch):
        monkeypatch.setenv("QUEUE_BACKEND", "arq")
        assert get_backend() == "arq"

    def test_redis_alias(self, monkeypatch):
        monkeypatch.setenv("QUEUE_BACKEND", "redis")
        assert get_backend() == "arq"

    def test_unknown_value_falls_back_to_thread(self, monkeypatch):
        monkeypatch.setenv("QUEUE_BACKEND", "kafka")
        assert get_backend() == "thread"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("QUEUE_BACKEND", "ARQ")
        assert get_backend() == "arq"


class TestRedisUrl:
    def test_default(self):
        assert get_redis_url() == "redis://localhost:6379"

    def test_overridden(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://prod-host:6379/3")
        assert get_redis_url() == "redis://prod-host:6379/3"


class TestEnqueueJobGuard:
    def test_raises_when_backend_not_arq(self):
        # Helps catch coding mistakes — caller should branch on get_backend()
        with pytest.raises(RuntimeError, match="QUEUE_BACKEND is not 'arq'"):
            enqueue_job("job-123")

    def test_runs_async_path_when_backend_is_arq(self, monkeypatch):
        monkeypatch.setenv("QUEUE_BACKEND", "arq")

        called_with = []

        async def fake_async(job_id: str):
            called_with.append(job_id)

        monkeypatch.setattr("src.web.queue._enqueue_async", fake_async)
        enqueue_job("job-456")
        assert called_with == ["job-456"]


class TestDispatchJob:
    """Verify the seam in app.py routes to ARQ vs threading correctly."""

    def test_threading_path_when_backend_default(self, monkeypatch):
        from src.web import app as app_mod

        # Stub the threading.Thread to confirm it's used
        thread_starts = []

        class FakeThread:
            def __init__(self, *args, **kwargs):
                thread_starts.append(kwargs)
            def start(self):
                pass

        monkeypatch.setattr(app_mod.threading, "Thread", FakeThread)
        # ARQ enqueue must NOT be called
        arq_calls = []
        monkeypatch.setattr(app_mod, "arq_enqueue_job", lambda job_id: arq_calls.append(job_id))

        app_mod._dispatch_job("job-1")

        assert len(thread_starts) == 1
        assert arq_calls == []

    def test_arq_path_when_backend_set(self, monkeypatch):
        from src.web import app as app_mod

        monkeypatch.setenv("QUEUE_BACKEND", "arq")

        thread_starts = []
        class FakeThread:
            def __init__(self, *args, **kwargs):
                thread_starts.append(kwargs)
            def start(self):
                pass
        monkeypatch.setattr(app_mod.threading, "Thread", FakeThread)

        arq_calls = []
        monkeypatch.setattr(app_mod, "arq_enqueue_job", lambda job_id: arq_calls.append(job_id))

        app_mod._dispatch_job("job-1")

        assert arq_calls == ["job-1"]
        assert len(thread_starts) == 0

    def test_arq_failure_falls_back_to_threading(self, monkeypatch):
        """If ARQ enqueue raises, we still get the job processed via threading.
        Belt-and-suspenders against transient Redis outages."""
        from src.web import app as app_mod

        monkeypatch.setenv("QUEUE_BACKEND", "arq")

        thread_starts = []
        class FakeThread:
            def __init__(self, *args, **kwargs):
                thread_starts.append(kwargs)
            def start(self):
                pass
        monkeypatch.setattr(app_mod.threading, "Thread", FakeThread)

        def boom(job_id):
            raise RuntimeError("Redis is down")
        monkeypatch.setattr(app_mod, "arq_enqueue_job", boom)

        app_mod._dispatch_job("job-1")

        # Threading path took over
        assert len(thread_starts) == 1
