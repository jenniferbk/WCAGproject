"""Integration tests for web auth endpoints and protected routes."""

import pytest
from fastapi.testclient import TestClient

from src.web.jobs import _local, init_db
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
    init_users_db()
    yield


@pytest.fixture
def client():
    from src.web.app import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_client(client):
    """Client with a registered and logged-in user."""
    res = client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "testpass123",
        "display_name": "Test User",
    })
    assert res.status_code == 200
    return client


def _register(client, email="user@example.com", password="password123", display_name=""):
    return client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "display_name": display_name,
    })


class TestRegistration:
    def test_register_success(self, client):
        res = _register(client)
        assert res.status_code == 200
        data = res.json()
        assert data["user"]["email"] == "user@example.com"
        assert "session" in res.cookies

    def test_register_sets_cookie(self, client):
        res = _register(client)
        assert "session" in res.cookies

    def test_register_missing_email(self, client):
        res = client.post("/api/auth/register", json={"password": "test1234"})
        assert res.status_code == 400

    def test_register_missing_password(self, client):
        res = client.post("/api/auth/register", json={"email": "a@b.com"})
        assert res.status_code == 400

    def test_register_short_password(self, client):
        res = _register(client, password="short")
        assert res.status_code == 400
        assert "8 characters" in res.json()["error"]

    def test_register_invalid_email(self, client):
        res = _register(client, email="notanemail")
        assert res.status_code == 400
        assert "Invalid email" in res.json()["error"]

    def test_register_duplicate_email(self, client):
        _register(client, email="dup@example.com")
        res = _register(client, email="dup@example.com")
        assert res.status_code == 409

    def test_register_with_display_name(self, client):
        res = _register(client, display_name="My Name")
        assert res.json()["user"]["display_name"] == "My Name"

    def test_register_default_display_name(self, client):
        res = _register(client, email="john@example.com")
        assert res.json()["user"]["display_name"] == "john"


class TestLogin:
    def test_login_success(self, client):
        _register(client, email="login@example.com", password="pass1234")
        # Clear the registration cookie
        client.cookies.clear()

        res = client.post("/api/auth/login", json={
            "email": "login@example.com",
            "password": "pass1234",
        })
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "login@example.com"
        assert "session" in res.cookies

    def test_login_wrong_password(self, client):
        _register(client, email="login@example.com", password="correct")
        client.cookies.clear()

        res = client.post("/api/auth/login", json={
            "email": "login@example.com",
            "password": "wrong",
        })
        assert res.status_code == 401

    def test_login_nonexistent_user(self, client):
        res = client.post("/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "pass1234",
        })
        assert res.status_code == 401

    def test_login_missing_fields(self, client):
        res = client.post("/api/auth/login", json={})
        assert res.status_code == 400


class TestLogout:
    def test_logout_clears_cookie(self, auth_client):
        res = auth_client.post("/api/auth/logout")
        assert res.status_code == 200

        # After logout, /me should return 401
        me_res = auth_client.get("/api/auth/me")
        assert me_res.status_code == 401


class TestMe:
    def test_me_authenticated(self, auth_client):
        res = auth_client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "test@example.com"

    def test_me_unauthenticated(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401


class TestProtectedEndpoints:
    def test_upload_requires_auth(self, client):
        res = client.post("/api/upload", files={"file": ("test.docx", b"content")})
        assert res.status_code == 401

    def test_jobs_requires_auth(self, client):
        res = client.get("/api/jobs")
        assert res.status_code == 401

    def test_job_status_requires_auth(self, client):
        res = client.get("/api/jobs/abc123")
        assert res.status_code == 401

    def test_report_requires_auth(self, client):
        res = client.get("/api/jobs/abc123/report")
        assert res.status_code == 401

    def test_download_requires_auth(self, client):
        res = client.get("/api/jobs/abc123/download")
        assert res.status_code == 401

    def test_download_original_requires_auth(self, client):
        res = client.get("/api/jobs/abc123/download-original")
        assert res.status_code == 401


class TestJobOwnership:
    def test_cant_see_other_users_jobs(self, client):
        # Register user 1 and upload
        _register(client, email="user1@example.com")
        res = client.post("/api/upload", files={"file": ("test.docx", b"PK\x03\x04")},
                          data={"course_name": "", "department": ""})
        if res.status_code == 200:
            job_id = res.json()["job_id"]

            # Register user 2
            client.cookies.clear()
            _register(client, email="user2@example.com")

            # User 2 shouldn't see user 1's job
            res2 = client.get(f"/api/jobs/{job_id}")
            assert res2.status_code == 404

    def test_jobs_filtered_by_user(self, client):
        # Register user 1
        _register(client, email="user1@example.com")
        res = client.get("/api/jobs")
        assert res.status_code == 200
        assert res.json()["jobs"] == []


class TestUsageLimits:
    def test_upload_tracks_file_type(self, auth_client):
        res = auth_client.post(
            "/api/upload",
            files={"file": ("bad.txt", b"content")},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 400
        assert "Unsupported file type" in res.json()["error"]

    def test_file_size_limit(self, auth_client, monkeypatch):
        """Files exceeding user's max size are rejected."""
        from src.web import users as users_mod

        # Set max to 1 byte for testing
        user_before = auth_client.get("/api/auth/me").json()["user"]
        users_mod.update_user(user_before["id"], max_file_size_mb=0)

        res = auth_client.post(
            "/api/upload",
            files={"file": ("test.docx", b"PK\x03\x04some content")},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 413

    def test_document_count_limit(self, auth_client):
        """After max documents, uploads are rejected with 403."""
        from src.web import users as users_mod

        user_data = auth_client.get("/api/auth/me").json()["user"]
        # Set documents_used to max
        users_mod.update_user(user_data["id"], documents_used=3)

        res = auth_client.post(
            "/api/upload",
            files={"file": ("test.docx", b"PK\x03\x04")},
            data={"course_name": "", "department": ""},
        )
        assert res.status_code == 403
        assert "limit" in res.json()["error"].lower()

    def test_usage_count_in_me(self, auth_client):
        """The /me endpoint returns current usage counts."""
        res = auth_client.get("/api/auth/me")
        user = res.json()["user"]
        assert user["documents_used"] == 0
        assert user["max_documents"] == 3
        assert user["max_file_size_mb"] == 20
        assert user["tier"] == "free"
