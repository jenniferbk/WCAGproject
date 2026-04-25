"""E2E: request-ID middleware + health endpoint."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import wait_for_job

pytestmark = pytest.mark.e2e


def _docx_bytes() -> bytes:
    return b"PK\x03\x04" + b"\x00" * 100


class TestRequestId:
    def test_every_response_has_request_id(self, client):
        for path in ("/api/health", "/", "/robots.txt"):
            res = client.get(path)
            assert "X-Request-ID" in res.headers, f"{path} missing X-Request-ID"

    def test_each_request_gets_unique_id(self, client):
        ids = {client.get("/api/health").headers["X-Request-ID"] for _ in range(5)}
        assert len(ids) == 5

    def test_upstream_request_id_honored(self, client):
        custom = "trace-from-caddy-12345"
        res = client.get("/api/health", headers={"X-Request-ID": custom})
        assert res.headers["X-Request-ID"] == custom


class TestHealthEndpoint:
    def test_health_is_public(self, client):
        # No auth, no error
        res = client.get("/api/health")
        assert res.status_code == 200

    def test_health_reports_queue_depth(self, auth_client, stub_process):
        # Health on empty queue first
        h0 = auth_client.get("/api/health").json()
        assert h0["queue"]["queued"] == 0
        assert h0["queue"]["processing"] == 0

        # Queue something
        res = auth_client.post(
            "/api/upload",
            files={"file": ("doc.docx", _docx_bytes())},
            data={"course_name": "", "department": ""},
        )
        wait_for_job(auth_client, res.json()["job_id"])

        # After completion, queue should be empty again
        h1 = auth_client.get("/api/health").json()
        assert h1["queue"]["queued"] == 0
        assert h1["queue"]["processing"] == 0

    def test_health_reports_disk_free(self, client):
        h = client.get("/api/health").json()
        assert isinstance(h["disk_free_mb"], int)
        # Disk should have SOME free space on any machine running tests
        assert h["disk_free_mb"] > 0

    def test_health_returns_version(self, client):
        h = client.get("/api/health").json()
        assert h["version"] == "0.1.0"


class TestRobotsAndSitemap:
    """Quick sanity checks on the SEO-facing endpoints — they should
    keep working when middleware/observability changes ship."""

    def test_robots_txt_present_and_non_empty(self, client):
        res = client.get("/robots.txt")
        assert res.status_code == 200
        assert "User-agent" in res.text
        assert "Disallow: /api/" in res.text

    def test_sitemap_responds(self, client):
        res = client.get("/sitemap.xml")
        assert res.status_code == 200
