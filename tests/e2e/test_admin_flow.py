"""E2E: admin endpoints — user list, user update, cost-status, retention cleanup."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import wait_for_job

pytestmark = pytest.mark.e2e


def _docx_bytes() -> bytes:
    return b"PK\x03\x04" + b"\x00" * 100


class TestAdminAccess:
    def test_non_admin_cannot_list_users(self, auth_client):
        res = auth_client.get("/api/admin/users")
        assert res.status_code in (401, 403)

    def test_admin_can_list_users(self, admin_client, auth_client):
        # auth_client (regular user) registered first; admin_client second
        res = admin_client.get("/api/admin/users")
        assert res.status_code == 200
        emails = {u["email"] for u in res.json()["users"]}
        assert "admin@example.com" in emails
        assert "user@example.com" in emails


class TestAdminUserUpdate:
    def test_admin_can_update_user_pages_balance(self, admin_client, auth_client):
        me = auth_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"pages_balance": 999},
        )
        assert res.status_code == 200
        # Verify
        fresh = admin_client.get(f"/api/admin/users/{me['id']}")
        assert fresh.json()["user"]["pages_balance"] == 999

    def test_admin_can_promote_user(self, admin_client, auth_client):
        me = auth_client.get("/api/auth/me").json()["user"]
        assert me["is_admin"] is False
        admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"is_admin": True},
        )
        # Now the regular user should be admin
        promoted = auth_client.get("/api/auth/me").json()["user"]
        assert promoted["is_admin"] is True


class TestCostStatusEndpoint:
    def test_admin_can_read_cost_status(self, admin_client):
        res = admin_client.get("/api/admin/cost-status")
        assert res.status_code == 200
        data = res.json()
        assert set(data.keys()) == {
            "allowed", "reason", "daily_cost_usd", "weekly_cost_usd",
            "daily_cap_usd", "weekly_cap_usd", "kill_switch",
        }

    def test_non_admin_cannot_read_cost_status(self, auth_client):
        res = auth_client.get("/api/admin/cost-status")
        assert res.status_code in (401, 403)

    def test_kill_switch_reflected_in_status(self, admin_client, monkeypatch):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        res = admin_client.get("/api/admin/cost-status")
        assert res.json()["kill_switch"] is True
        assert res.json()["allowed"] is False
        assert res.json()["reason"] == "kill_switch"

    def test_completed_job_cost_appears_in_daily_total(
        self, admin_client, auth_client, stub_process,
    ):
        # Submit + finish a job
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        wait_for_job(auth_client, res.json()["job_id"])

        status = admin_client.get("/api/admin/cost-status").json()
        assert status["daily_cost_usd"] > 0
        assert status["daily_cost_usd"] == pytest.approx(stub_process["cost_usd"], rel=0.01)


class TestRetentionEndpoint:
    def test_admin_can_trigger_cleanup(self, admin_client):
        res = admin_client.post("/api/admin/retention/cleanup")
        assert res.status_code == 200
        report = res.json()
        assert "files_scanned" in report
        assert "files_deleted" in report
        assert "started_at" in report
        assert "finished_at" in report

    def test_non_admin_cannot_trigger_cleanup(self, auth_client):
        res = auth_client.post("/api/admin/retention/cleanup")
        assert res.status_code in (401, 403)
