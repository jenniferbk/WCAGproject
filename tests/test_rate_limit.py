"""Tests for the rate limiting module."""

import time

import pytest
from fastapi.testclient import TestClient

from src.web.jobs import _local, init_db
from src.web.rate_limit import RateLimiter, reset_limiter
from src.web.users import init_users_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp database for each test."""
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    if hasattr(_local, "conn"):
        _local.conn = None

    monkeypatch.setenv("ADMIN_EMAILS", "")
    init_db()
    init_users_db()
    yield


@pytest.fixture(autouse=True)
def reset_global_limiter():
    """Reset global rate limiter state between tests."""
    reset_limiter()
    yield
    reset_limiter()


@pytest.fixture
def client():
    from src.web.app import app
    return TestClient(app, raise_server_exceptions=False)


# ── Unit tests for RateLimiter ──────────────────────────────────


class TestRateLimiter:
    def test_allows_up_to_limit(self):
        limiter = RateLimiter()
        for i in range(5):
            allowed, retry = limiter.is_allowed("key", 5, 60)
            assert allowed, f"Request {i+1} should be allowed"
            assert retry == 0

    def test_blocks_after_limit(self):
        limiter = RateLimiter()
        for _ in range(5):
            limiter.is_allowed("key", 5, 60)

        allowed, retry = limiter.is_allowed("key", 5, 60)
        assert not allowed
        assert retry > 0

    def test_different_keys_independent(self):
        limiter = RateLimiter()
        for _ in range(5):
            limiter.is_allowed("key_a", 5, 60)

        # key_a is exhausted
        allowed_a, _ = limiter.is_allowed("key_a", 5, 60)
        assert not allowed_a

        # key_b should still work
        allowed_b, _ = limiter.is_allowed("key_b", 5, 60)
        assert allowed_b

    def test_window_expiry(self):
        limiter = RateLimiter()
        # Use a very short window
        for _ in range(3):
            limiter.is_allowed("key", 3, 1)

        # Should be blocked
        allowed, _ = limiter.is_allowed("key", 3, 1)
        assert not allowed

        # Wait for the window to expire
        time.sleep(1.1)

        # Should be allowed again
        allowed, _ = limiter.is_allowed("key", 3, 1)
        assert allowed

    def test_reset_clears_state(self):
        limiter = RateLimiter()
        for _ in range(5):
            limiter.is_allowed("key", 5, 60)

        allowed, _ = limiter.is_allowed("key", 5, 60)
        assert not allowed

        limiter.reset()

        allowed, _ = limiter.is_allowed("key", 5, 60)
        assert allowed

    def test_cleanup_removes_empty_keys(self):
        limiter = RateLimiter()
        limiter._cleanup_interval = 0  # Force cleanup on every call

        # Add a key then manually clear its timestamps to simulate expiry
        limiter.is_allowed("stale_key", 10, 60)
        with limiter._lock:
            limiter._windows["stale_key"] = []  # Simulate fully expired

        # Next call triggers cleanup
        limiter.is_allowed("fresh_key", 10, 60)

        with limiter._lock:
            assert "stale_key" not in limiter._windows
            assert "fresh_key" in limiter._windows


# ── Integration tests with FastAPI endpoints ────────────────────


class TestRateLimitEndpoints:
    def test_login_rate_limit(self, client):
        """Login should be limited to 10/min per IP."""
        for i in range(10):
            res = client.post("/api/auth/login", json={
                "email": "no@no.com", "password": "wrong"
            })
            assert res.status_code in (400, 401), f"Request {i+1} should be allowed"

        # 11th request should be rate limited
        res = client.post("/api/auth/login", json={
            "email": "no@no.com", "password": "wrong"
        })
        assert res.status_code == 429
        assert "Retry-After" in res.headers

    def test_register_rate_limit(self, client):
        """Registration should be limited to 5/hour per IP."""
        for i in range(5):
            res = client.post("/api/auth/register", json={
                "email": f"user{i}@test.com",
                "password": "testpass123",
            })
            assert res.status_code == 200, f"Request {i+1} should be allowed"

        # 6th registration attempt should be rate limited
        res = client.post("/api/auth/register", json={
            "email": "extra@test.com",
            "password": "testpass123",
        })
        assert res.status_code == 429

    def test_forgot_password_rate_limit(self, client):
        """Forgot password should be limited to 5/hour per IP."""
        for i in range(5):
            res = client.post("/api/auth/forgot-password", json={
                "email": f"user{i}@test.com"
            })
            assert res.status_code == 200

        res = client.post("/api/auth/forgot-password", json={
            "email": "extra@test.com"
        })
        assert res.status_code == 429

    def test_rate_limit_retry_after_header(self, client):
        """429 responses should include Retry-After header."""
        for _ in range(10):
            client.post("/api/auth/login", json={
                "email": "no@no.com", "password": "wrong"
            })

        res = client.post("/api/auth/login", json={
            "email": "no@no.com", "password": "wrong"
        })
        assert res.status_code == 429
        retry = int(res.headers["Retry-After"])
        assert retry > 0

    def test_health_not_rate_limited(self, client):
        """Health check endpoint should not be rate limited."""
        for _ in range(200):
            res = client.get("/api/health")
            assert res.status_code == 200

    def test_x_forwarded_for_respected(self, client):
        """Rate limiter should use X-Forwarded-For when present."""
        # Exhaust limit for IP "1.2.3.4"
        for _ in range(10):
            client.post(
                "/api/auth/login",
                json={"email": "no@no.com", "password": "wrong"},
                headers={"X-Forwarded-For": "1.2.3.4"},
            )

        # Same IP should be blocked
        res = client.post(
            "/api/auth/login",
            json={"email": "no@no.com", "password": "wrong"},
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert res.status_code == 429

        # Different IP should still work
        res = client.post(
            "/api/auth/login",
            json={"email": "no@no.com", "password": "wrong"},
            headers={"X-Forwarded-For": "5.6.7.8"},
        )
        assert res.status_code in (400, 401)
