"""E2E: cost cap, per-user caps, page balance, and rate-limit interactions."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import wait_for_job

pytestmark = pytest.mark.e2e


def _docx_bytes() -> bytes:
    return b"PK\x03\x04" + b"\x00" * 100


class TestCostCapKillSwitch:
    def test_kill_switch_blocks_uploads_with_503(
        self, auth_client, stub_process, monkeypatch,
    ):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 503
        assert res.json()["reason"] == "kill_switch"

    def test_no_cap_allows_uploads(self, auth_client, stub_process):
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 200


class TestCostCapDailyCeiling:
    def test_daily_cap_blocks_after_threshold(
        self, auth_client, admin_client, stub_process, monkeypatch,
    ):
        # Set cap so the FIRST job fits but the SECOND would exceed.
        # Mock cost is ~$0.0045 per job (10k Gemini in + 2k out + 3k Claude in + 500 out).
        monkeypatch.setenv("COST_CAP_DAILY_USD", "0.001")

        # First upload: even though cap is below per-job cost, current_status()
        # checks BEFORE the job runs, when daily total is still 0 — so it passes.
        res1 = auth_client.post(
            "/api/upload",
            files={"file": ("a.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res1.status_code == 200
        wait_for_job(auth_client, res1.json()["job_id"])

        # Second upload: now daily total is over cap, so it blocks
        res2 = auth_client.post(
            "/api/upload",
            files={"file": ("b.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res2.status_code == 503
        assert res2.json()["reason"] == "daily_cap_exceeded"


class TestPerUserConcurrentCap:
    def test_concurrent_cap_blocks_third_upload(
        self, auth_client, admin_client, stub_process, monkeypatch,
    ):
        # Pin user pages high so we don't hit pages_balance first
        me = auth_client.get("/api/auth/me").json()["user"]
        admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"pages_balance": 10000},
        )

        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "2")

        # Block the mock: first job will sit in 'processing' until released
        import threading
        import time as _time
        gate = threading.Event()

        from src.models.pipeline import RemediationResult, CostSummary
        from pathlib import Path

        def slow_process(request, on_phase=None):
            gate.wait(timeout=5)
            out_dir = Path(request.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "out.pdf"
            out_path.write_bytes(b"%PDF-1.4\n")
            return RemediationResult(
                success=True, input_path=request.document_path,
                output_path=str(out_path), report_path="",
                issues_before=1, issues_after=0, issues_fixed=1,
                processing_time_seconds=0.01, cost_summary=CostSummary(),
            )

        import src.web.app as app_mod
        monkeypatch.setattr(app_mod, "process", slow_process)

        # Two uploads should accept (filling concurrent cap of 2). Brief sleep
        # between each lets daemon threads update job status before the next
        # cap check reads the queue depth.
        for i in range(2):
            r = auth_client.post(
                "/api/upload",
                files={"file": (f"d{i}.docx", _docx_bytes())},
                data={"course_name": "", "department": ""},
            )
            assert r.status_code == 200, r.text
            _time.sleep(0.1)

        # Verify both prior jobs are in queued/processing state before test
        listing = auth_client.get("/api/jobs").json()["jobs"]
        active = [j for j in listing if j["status"] in ("queued", "processing")]
        assert len(active) == 2, f"expected 2 active, got {[j['status'] for j in listing]}"

        # Third upload hits the cap
        r3 = auth_client.post(
            "/api/upload",
            files={"file": ("d3.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert r3.status_code == 429
        assert r3.json()["reason"] == "concurrent_cap"

        # Release the gate so the daemon threads finish
        gate.set()


class TestPageBalance:
    def test_insufficient_pages_rejected_with_403(
        self, auth_client, admin_client, stub_process,
    ):
        # Admin sets target user's pages to 0
        me = auth_client.get("/api/auth/me").json()["user"]
        admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"pages_balance": 0},
        )

        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 403
        assert "Insufficient" in res.json()["error"]


class TestCheckOrdering:
    """The four guard layers (cost cap → user caps → file size → pages) must
    fail in the right order so users see the most informative error."""

    def test_kill_switch_takes_precedence_over_user_caps(
        self, auth_client, stub_process, monkeypatch,
    ):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        monkeypatch.setenv("MAX_USER_CONCURRENT_JOBS", "0")  # also blocking
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        # Kill switch wins (503), not user caps (429)
        assert res.status_code == 503

    def test_unsupported_extension_caught_before_cost_check(
        self, auth_client, stub_process, monkeypatch,
    ):
        monkeypatch.setenv("COST_CAP_KILL_SWITCH", "1")
        # Bad extension should 400 before cost cap can 503
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.exe", b"binary")},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 400
