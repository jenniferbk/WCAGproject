"""Tests for the system-wide cost cap + kill switch (src/web/cost_cap.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.web.cost_cap import (
    check_can_submit,
    current_status,
    ensure_cost_column,
    record_job_cost,
)
from src.web.jobs import _get_conn, _local, create_job, init_db
from src.web.users import init_users_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp database for each test."""
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    if hasattr(_local, "conn"):
        _local.conn = None

    init_db()
    init_users_db()  # adds user_id, batch_id, etc. to jobs table
    ensure_cost_column()
    yield


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip any host cost-cap env vars so each test starts clean."""
    monkeypatch.delenv("COST_CAP_DAILY_USD", raising=False)
    monkeypatch.delenv("COST_CAP_WEEKLY_USD", raising=False)
    monkeypatch.delenv("COST_CAP_KILL_SWITCH", raising=False)


def _seed_job_cost(cost_usd: float, created_at: str | None = None) -> str:
    """Create a job and record a cost on it. Returns job_id."""
    job = create_job(filename="test.pdf", original_path="/tmp/test.pdf")
    if created_at:
        conn = _get_conn()
        conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (created_at, job.id))
        conn.commit()
    record_job_cost(job.id, cost_usd)
    return job.id


class TestEnsureCostColumn:
    def test_adds_column_if_missing(self):
        conn = _get_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "estimated_cost_usd" in cols

    def test_idempotent(self):
        ensure_cost_column()
        ensure_cost_column()
        conn = _get_conn()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert cols.count("estimated_cost_usd") == 1


class TestRecordJobCost:
    def test_records_cost(self):
        job_id = _seed_job_cost(0.42)
        conn = _get_conn()
        row = conn.execute(
            "SELECT estimated_cost_usd FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row[0] == pytest.approx(0.42)

    def test_overwrites_previous_cost(self):
        job_id = _seed_job_cost(0.10)
        record_job_cost(job_id, 0.99)
        conn = _get_conn()
        row = conn.execute(
            "SELECT estimated_cost_usd FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row[0] == pytest.approx(0.99)


class TestCurrentStatus:
    def test_no_caps_no_costs_allowed(self):
        s = current_status()
        assert s.allowed is True
        assert s.reason == "ok"
        assert s.daily_cost_usd == 0
        assert s.daily_cap_usd is None

    def test_kill_switch_blocks(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        s = current_status()
        assert s.allowed is False
        assert s.reason == "kill_switch"
        assert s.kill_switch is True

    def test_kill_switch_accepts_truthy_variants(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("COST_CAP_KILL_SWITCH", val)
            s = current_status()
            assert s.allowed is False, f"value {val!r} should activate kill switch"

    def test_kill_switch_off_for_other_values(self, monkeypatch):
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("COST_CAP_KILL_SWITCH", val)
            s = current_status()
            assert s.allowed is True, f"value {val!r} should NOT activate kill switch"

    def test_daily_cap_blocks_when_exceeded(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_DAILY_USD", "1.00")
        _seed_job_cost(1.50)  # over cap
        s = current_status()
        assert s.allowed is False
        assert s.reason == "daily_cap_exceeded"
        assert s.daily_cost_usd == pytest.approx(1.50)
        assert s.daily_cap_usd == 1.00

    def test_daily_cap_allows_when_under(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_DAILY_USD", "10.00")
        _seed_job_cost(2.50)
        s = current_status()
        assert s.allowed is True
        assert s.daily_cost_usd == pytest.approx(2.50)

    def test_daily_cap_at_exactly_zero_unlimited(self, monkeypatch):
        """COST_CAP_DAILY_USD=0 should mean unlimited (treat 0 like unset)."""
        monkeypatch.setenv("COST_CAP_DAILY_USD", "0")
        _seed_job_cost(99.99)
        s = current_status()
        assert s.allowed is True
        assert s.daily_cap_usd is None

    def test_daily_cap_invalid_treated_as_unlimited(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_DAILY_USD", "not-a-number")
        _seed_job_cost(99.99)
        s = current_status()
        assert s.allowed is True
        assert s.daily_cap_usd is None

    def test_weekly_cap_blocks_when_exceeded(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_WEEKLY_USD", "5.00")
        # Spread costs across the past few days, all within 7-day window
        now = datetime.now(timezone.utc)
        _seed_job_cost(2.0, created_at=(now - timedelta(days=1)).isoformat())
        _seed_job_cost(2.0, created_at=(now - timedelta(days=3)).isoformat())
        _seed_job_cost(2.0, created_at=(now - timedelta(days=5)).isoformat())
        s = current_status()
        assert s.allowed is False
        assert s.reason == "weekly_cap_exceeded"
        assert s.weekly_cost_usd == pytest.approx(6.0)

    def test_weekly_window_excludes_jobs_older_than_7_days(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_WEEKLY_USD", "5.00")
        now = datetime.now(timezone.utc)
        _seed_job_cost(99.0, created_at=(now - timedelta(days=10)).isoformat())  # too old
        _seed_job_cost(2.0)  # today
        s = current_status()
        assert s.allowed is True
        assert s.weekly_cost_usd == pytest.approx(2.0)

    def test_daily_window_excludes_yesterday(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_DAILY_USD", "5.00")
        now = datetime.now(timezone.utc)
        _seed_job_cost(99.0, created_at=(now - timedelta(days=1, hours=1)).isoformat())
        _seed_job_cost(2.0)
        s = current_status()
        assert s.allowed is True
        assert s.daily_cost_usd == pytest.approx(2.0)

    def test_kill_switch_takes_precedence_over_caps(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        monkeypatch.setenv("COST_CAP_DAILY_USD", "1000")  # cap not exceeded
        s = current_status()
        assert s.allowed is False
        assert s.reason == "kill_switch"

    def test_to_dict_includes_all_fields(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_DAILY_USD", "10.00")
        _seed_job_cost(2.50)
        d = current_status().to_dict()
        assert set(d.keys()) == {
            "allowed", "reason", "daily_cost_usd", "weekly_cost_usd",
            "daily_cap_usd", "weekly_cap_usd", "kill_switch",
        }
        assert d["daily_cost_usd"] == pytest.approx(2.50)
        assert d["daily_cap_usd"] == 10.00


class TestCheckCanSubmit:
    def test_ok_when_under_caps(self):
        s = check_can_submit()
        assert s.allowed is True
        assert s.reason == "ok"

    def test_blocks_when_kill_switch(self, monkeypatch):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        s = check_can_submit()
        assert s.allowed is False
        assert s.reason == "kill_switch"
