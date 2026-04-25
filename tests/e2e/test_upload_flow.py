"""E2E: full upload → process (mocked) → status → download flow."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import wait_for_job

pytestmark = pytest.mark.e2e


def _docx_bytes() -> bytes:
    """Smallest valid .docx-shaped payload — just enough magic for the upload
    endpoint to accept it. The mocked process() doesn't care about content."""
    return b"PK\x03\x04" + b"\x00" * 100


class TestUploadAcceptance:
    def test_unauth_upload_rejected(self, client):
        res = client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 401

    def test_unsupported_extension_rejected(self, auth_client):
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.exe", b"binary")},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 400
        assert "Unsupported" in res.json()["error"]

    def test_oversize_file_rejected(self, auth_client):
        # Default user max is 20MB; send 25MB
        big = b"x" * (25 * 1024 * 1024)
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", big)},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 413


class TestUploadSuccessFlow:
    def test_upload_creates_job_and_runs_to_completion(self, auth_client, stub_process):
        res = auth_client.post(
            "/api/upload",
            files={"file": ("syllabus.docx", _docx_bytes())},
            data={"course_name": "MATH 201", "department": "Mathematics"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "queued"
        job_id = body["job_id"]

        completed = wait_for_job(auth_client, job_id)
        assert completed["status"] == "completed"
        assert completed["issues_before"] == 10
        assert completed["issues_after"] == 2
        assert completed["issues_fixed"] == 8

        # The mock should have been called with the user's course context
        assert len(stub_process["calls"]) == 1
        req = stub_process["calls"][0]
        assert req.course_context.course_name == "MATH 201"
        assert req.course_context.department == "Mathematics"

    def test_completed_job_download_returns_file(self, auth_client, stub_process):
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        job_id = res.json()["job_id"]
        wait_for_job(auth_client, job_id)

        dl = auth_client.get(f"/api/jobs/{job_id}/download")
        assert dl.status_code == 200
        assert dl.content.startswith(b"%PDF")

    def test_completed_job_report_returns_html(self, auth_client, stub_process):
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        job_id = res.json()["job_id"]
        wait_for_job(auth_client, job_id)

        rep = auth_client.get(f"/api/jobs/{job_id}/report")
        assert rep.status_code == 200
        assert "<html>" in rep.text


class TestUploadFailureFlow:
    def test_pipeline_failure_marks_job_failed_and_refunds_pages(
        self, auth_client, stub_process,
    ):
        # Capture pages before
        me = auth_client.get("/api/auth/me").json()["user"]
        pages_before = me["pages_balance"]

        # Force the pipeline to fail
        stub_process["force_failure"] = True

        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        completed = wait_for_job(auth_client, job_id)
        assert completed["status"] == "failed"

        me_after = auth_client.get("/api/auth/me").json()["user"]
        # Pages should be refunded (pages_balance equal to before)
        assert me_after["pages_balance"] == pages_before


class TestJobIsolation:
    def test_user_cannot_see_another_users_job(self, client, stub_process):
        # User A uploads
        client.post("/api/auth/register", json={
            "email": "a@example.com", "password": "validpass123",
        })
        res_a = client.post(
            "/api/upload",
            files={"file": ("a.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        job_a = res_a.json()["job_id"]
        wait_for_job(client, job_a)

        # User B logs in
        client.post("/api/auth/logout")
        client.post("/api/auth/register", json={
            "email": "b@example.com", "password": "validpass123",
        })

        # B can't fetch A's job
        res = client.get(f"/api/jobs/{job_a}")
        assert res.status_code == 404

        # B can't download A's file
        dl = client.get(f"/api/jobs/{job_a}/download")
        assert dl.status_code == 404

        # B's job list doesn't include A's job
        listing = client.get("/api/jobs")
        ids = [j["id"] for j in listing.json()["jobs"]]
        assert job_a not in ids
