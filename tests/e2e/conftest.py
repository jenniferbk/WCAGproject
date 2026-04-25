"""Shared fixtures for the e2e regression suite.

These tests exercise the full FastAPI HTTP stack (middleware, auth, DB,
rate limits, cost cap, user caps, retention) but mock the orchestrator's
`process()` call so they don't burn API spend on Gemini/Claude or take
minutes per test. This keeps the suite cheap to run on every commit while
still catching regressions in the user-visible flows we care about.

Fixtures intentionally mirror the patterns in test_web_auth.py and
test_billing.py so engineers don't have to learn a new style for e2e.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.models.pipeline import (
    ApiUsage,
    ComprehensionResult,
    CostSummary,
    RemediationResult,
)
from src.web.jobs import _local, init_db
from src.web.rate_limit import reset_limiter
from src.web.users import init_users_db, update_user

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def fresh_db_and_dirs(tmp_path, monkeypatch):
    """Per-test isolation: temp DB, temp upload + output dirs, reset limiter."""
    import src.web.jobs as jobs_mod
    import src.web.app as app_mod

    db_path = tmp_path / "e2e.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "output"
    upload_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(app_mod, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(app_mod, "OUTPUT_DIR", output_dir)

    if hasattr(_local, "conn"):
        _local.conn = None

    monkeypatch.setenv("ADMIN_EMAILS", "")  # disable auto-promotion
    reset_limiter()
    init_db()
    init_users_db()

    # Strip any host env vars that would change behavior under test
    for var in (
        "COST_CAP_DAILY_USD", "COST_CAP_WEEKLY_USD", "COST_CAP_KILL_SWITCH",
        "MAX_USER_CONCURRENT_JOBS", "MAX_USER_JOBS_PER_HOUR",
        "MAX_CONCURRENT_JOBS",
        "RETENTION_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)

    yield


@pytest.fixture
def stub_process(monkeypatch):
    """Replace src.web.app.process with a fast deterministic stub.

    Writes a tiny stub PDF + report to the request's output_dir so that
    download endpoints find real files. Returns a RemediationResult with
    a known `cost_summary` so cost-cap tests can verify accounting.

    Yields a dict tracking calls so tests can assert on inputs:
        {"calls": [request], "force_failure": bool, "cost_usd": float}
    """
    state = {"calls": [], "force_failure": False, "cost_usd": 0.05}

    def _fake_process(request, on_phase=None):
        state["calls"].append(request)

        if on_phase:
            on_phase("comprehension", "stub")
            on_phase("strategy", "stub")
            on_phase("execute", "stub")
            on_phase("review", "stub")

        out_dir = Path(request.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        in_path = Path(request.document_path)
        out_path = out_dir / f"remediated_{in_path.name}"
        out_path.write_bytes(b"%PDF-1.4\n%stub\n")
        report_path = out_dir / "report.html"
        report_path.write_text("<html><body>stub report</body></html>")

        if state["force_failure"]:
            return RemediationResult(
                success=False,
                input_path=str(in_path),
                error="forced failure for test",
                processing_time_seconds=0.01,
                cost_summary=CostSummary(usage_records=[]),
            )

        # ~$0.05 of mocked spend, split across two model calls
        usage = [
            ApiUsage(phase="comprehension", model="gemini-2.5-flash",
                     input_tokens=10_000, output_tokens=2_000),
            ApiUsage(phase="strategy", model="claude-sonnet-4-5",
                     input_tokens=3_000, output_tokens=500),
        ]
        cost = CostSummary(usage_records=usage)
        # Override to a deterministic value the tests can check against
        # (mutating frozen pydantic models isn't possible — instead we
        # carry the expected value via state and tests use that)
        state["cost_usd"] = cost.estimated_cost_usd

        return RemediationResult(
            success=True,
            input_path=str(in_path),
            output_path=str(out_path),
            report_path=str(report_path),
            issues_before=10,
            issues_after=2,
            issues_fixed=8,
            comprehension=ComprehensionResult(),
            items_for_human_review=[],
            processing_time_seconds=0.01,
            cost_summary=cost,
        )

    import src.web.app as app_mod
    monkeypatch.setattr(app_mod, "process", _fake_process)
    return state


@pytest.fixture
def client(stub_process):
    from src.web.app import app
    return TestClient(app, raise_server_exceptions=False)


def _register(client, email="user@example.com", password="testpass123", display_name="Test User"):
    return client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": display_name,
    })


@pytest.fixture
def auth_client(client):
    """Client logged in as a regular user."""
    res = _register(client)
    assert res.status_code == 200, res.text
    return client


@pytest.fixture
def admin_client(stub_process):
    """SEPARATE TestClient logged in as admin (own cookie jar — must not
    share with auth_client or whichever registers last wins)."""
    from src.web.app import app
    c = TestClient(app, raise_server_exceptions=False)
    res = _register(c, email="admin@example.com", display_name="Admin")
    assert res.status_code == 200, res.text
    user_id = res.json()["user"]["id"]
    update_user(user_id, is_admin=True)
    return c


def wait_for_job(client, job_id: str, terminal=("completed", "failed"), timeout: float = 10.0) -> dict:
    """Poll /api/jobs/{id} until status is terminal. Used by upload-flow tests
    where a daemon thread runs the (mocked) pipeline asynchronously."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/api/jobs/{job_id}")
        if res.status_code == 200:
            data = res.json().get("job", res.json())
            status = data.get("status")
            if status in terminal:
                return data
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach terminal state within {timeout}s")
