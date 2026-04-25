"""Tests for request-ID middleware + structured logging filter."""

from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient

from src.web.jobs import _local, init_db
from src.web.observability import (
    RequestIdFilter,
    configure_logging,
    request_id_var,
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


@pytest.fixture
def client():
    from src.web.app import app
    return TestClient(app)


class TestRequestIdMiddleware:
    def test_assigns_request_id_when_none_provided(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        assert "X-Request-ID" in res.headers
        # UUID4 hex is 32 chars
        assert len(res.headers["X-Request-ID"]) == 32

    def test_each_request_gets_unique_id(self, client):
        ids = {client.get("/api/health").headers["X-Request-ID"] for _ in range(5)}
        assert len(ids) == 5

    def test_accepts_upstream_request_id(self, client):
        upstream = "caddy-12345-abcdef"
        res = client.get("/api/health", headers={"X-Request-ID": upstream})
        assert res.headers["X-Request-ID"] == upstream

    def test_rejects_oversized_upstream_id(self, client):
        huge = "x" * 200
        res = client.get("/api/health", headers={"X-Request-ID": huge})
        assert res.headers["X-Request-ID"] != huge
        assert len(res.headers["X-Request-ID"]) == 32

    def test_rejects_non_printable_id(self, client):
        bad = "abc\x00\x01"
        res = client.get("/api/health", headers={"X-Request-ID": bad})
        assert res.headers["X-Request-ID"] != bad


class TestRequestIdFilter:
    def test_filter_injects_request_id(self):
        f = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py", lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        token = request_id_var.set("test-id-123")
        try:
            f.filter(record)
        finally:
            request_id_var.reset(token)
        assert record.request_id == "test-id-123"

    def test_filter_uses_dash_when_outside_request(self):
        f = RequestIdFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py", lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        f.filter(record)
        assert record.request_id == "-"


class TestConfigureLogging:
    def test_configure_is_idempotent(self):
        configure_logging()
        before = len(logging.getLogger().handlers)
        configure_logging()
        after = len(logging.getLogger().handlers)
        assert before == after


class TestHealthEndpoint:
    def test_health_returns_expected_fields(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("ok", "degraded")
        assert data["db"] in ("ok", "error")
        assert "queue" in data
        assert "queued" in data["queue"]
        assert "processing" in data["queue"]
        assert "disk_free_mb" in data
        assert "version" in data

    def test_health_reports_zero_queue_when_empty(self, client):
        res = client.get("/api/health")
        data = res.json()
        assert data["queue"]["queued"] == 0
        assert data["queue"]["processing"] == 0
