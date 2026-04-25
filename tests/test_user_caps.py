"""Tests for per-user job caps (src/web/user_caps.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.web.jobs import _get_conn, _local, create_job, init_db, update_job
from src.web.user_caps import (
    DEFAULT_CONCURRENT,
    DEFAULT_HOURLY,
    check_user_caps,
    count_active_jobs,
    count_recent_jobs,
)
from src.web.users import init_users_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    if hasattr(_local, "conn"):
        _local.conn = None

    init_db()
    init_users_db()
    yield


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MAX_USER_CONCURRENT_JOBS", raising=False)
    monkeypatch.delenv("MAX_USER_JOBS_PER_HOUR", raising=False)


def _make_job(user_id: str, status: str = "queued", created_offset_minutes: int = 0):
    """Helper to create a job for a user with a specific status and creation time."""
    job = create_job(filename="x.pdf", original_path="/tmp/x.pdf", user_id=user_id)
    update_job(job.id, status=status)
    if created_offset_minutes:
        ts = (datetime.now(timezone.utc) + timedelta(minutes=created_offset_minutes)).isoformat()
        conn = _get_conn()
        conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (ts, job.id))
        conn.commit()
    return job


class TestCounts:
    def test_count_active_includes_queued(self):
        _make_job("u1", "queued")
        _make_job("u1", "queued")
        assert count_active_jobs("u1") == 2

    def test_count_active_includes_processing(self):
        _make_job("u1", "queued")
        _make_job("u1", "processing")
        assert count_active_jobs("u1") == 2

    def test_count_active_excludes_completed_and_failed(self):
        _make_job("u1", "queued")
        _make_job("u1", "completed")
        _make_job("u1", "failed")
        assert count_active_jobs("u1") == 1

    def test_count_active_isolates_by_user(self):
        _make_job("u1", "queued")
        _make_job("u2", "queued")
        assert count_active_jobs("u1") == 1
        assert count_active_jobs("u2") == 1

    def test_count_active_empty_user_id_zero(self):
        _make_job("u1", "queued")
        assert count_active_jobs("") == 0

    def test_count_recent_only_within_window(self):
        _make_job("u1", "completed", created_offset_minutes=-30)   # 30min ago
        _make_job("u1", "completed", created_offset_minutes=-90)   # 90min ago — outside
        _make_job("u1", "completed", created_offset_minutes=-10)   # 10min ago
        assert count_recent_jobs("u1", hours=1) == 2

    def test_count_recent_includes_all_statuses(self):
        _make_job("u1", "queued", created_offset_minutes=-10)
        _make_job("u1", "completed", created_offset_minutes=-20)
        _make_job("u1", "failed", created_offset_minutes=-30)
        assert count_recent_jobs("u1", hours=1) == 3


class TestCheckUserCaps:
    def test_default_caps_allow_under_limit(self):
        # User with 2 active and 5 in last hour
        for _ in range(2):
            _make_job("u1", "queued")
        for _ in range(3):
            _make_job("u1", "completed", created_offset_minutes=-10)

        s = check_user_caps("u1", is_admin=False)
        assert s.allowed is True
        assert s.reason == "ok"
        assert s.concurrent_jobs == 2
        assert s.hourly_jobs == 5
        assert s.concurrent_cap == DEFAULT_CONCURRENT
        assert s.hourly_cap == DEFAULT_HOURLY

    def test_concurrent_cap_blocks(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "2")
        for _ in range(2):
            _make_job("u1", "processing")

        s = check_user_caps("u1", is_admin=False)
        assert s.allowed is False
        assert s.reason == "concurrent_cap"
        assert s.concurrent_jobs == 2
        assert s.concurrent_cap == 2

    def test_hourly_cap_blocks(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_HOURLY_JOBS", "5")  # wrong name to confirm it's ignored
        monkeypatch.setenv("MAX_USER_JOBS_PER_HOUR", "5")
        for _ in range(5):
            _make_job("u1", "completed", created_offset_minutes=-10)

        s = check_user_caps("u1", is_admin=False)
        assert s.allowed is False
        assert s.reason == "hourly_cap"
        assert s.hourly_cap == 5

    def test_admin_bypasses_concurrent(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "1")
        for _ in range(5):
            _make_job("u1", "processing")

        s = check_user_caps("u1", is_admin=True)
        assert s.allowed is True
        assert s.reason == "ok"

    def test_admin_bypasses_hourly(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_JOBS_PER_HOUR", "1")
        for _ in range(50):
            _make_job("u1", "completed", created_offset_minutes=-30)

        s = check_user_caps("u1", is_admin=True)
        assert s.allowed is True

    def test_zero_concurrent_cap_unlimited(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "0")
        for _ in range(20):
            _make_job("u1", "processing")
        s = check_user_caps("u1", is_admin=False)
        assert s.allowed is True
        assert s.concurrent_cap is None

    def test_invalid_value_unlimited(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "abc")
        for _ in range(20):
            _make_job("u1", "processing")
        s = check_user_caps("u1", is_admin=False)
        assert s.allowed is True
        assert s.concurrent_cap is None

    def test_concurrent_takes_precedence_over_hourly(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "1")
        monkeypatch.setenv("MAX_USER_JOBS_PER_HOUR", "1")
        _make_job("u1", "processing")
        # User has 1 in-flight (hits concurrent) AND 1 in last hour (hits hourly)
        s = check_user_caps("u1", is_admin=False)
        assert s.allowed is False
        assert s.reason == "concurrent_cap"

    def test_to_dict_carries_state(self, monkeypatch):
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "10")
        _make_job("u1", "queued")
        d = check_user_caps("u1", is_admin=False).to_dict()
        assert set(d.keys()) == {
            "allowed", "reason", "concurrent_jobs", "hourly_jobs",
            "concurrent_cap", "hourly_cap",
        }
