"""Integration tests for web auth endpoints and protected routes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web.jobs import _local, init_db
from src.web.users import init_users_db, update_user


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp database for each test."""
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    if hasattr(_local, "conn"):
        _local.conn = None

    # Disable auto-promotion in tests so we control admin status explicitly
    monkeypatch.setenv("ADMIN_EMAILS", "")

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


@pytest.fixture
def admin_client(client):
    """Client with a registered, logged-in admin user."""
    res = client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "password": "adminpass123",
        "display_name": "Admin User",
    })
    assert res.status_code == 200
    user_id = res.json()["user"]["id"]
    update_user(user_id, is_admin=True)
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


class TestAdminMiddleware:
    def test_non_admin_gets_403(self, auth_client):
        """Non-admin users get 403 on admin endpoints."""
        res = auth_client.get("/api/admin/users")
        assert res.status_code == 403
        assert "Admin" in res.json()["detail"]

    def test_unauthenticated_gets_401(self, client):
        """Unauthenticated requests get 401 on admin endpoints."""
        res = client.get("/api/admin/users")
        assert res.status_code == 401

    def test_admin_allowed(self, admin_client):
        """Admin users can access admin endpoints."""
        res = admin_client.get("/api/admin/users")
        assert res.status_code == 200


class TestAdminListUsers:
    def test_list_users(self, admin_client, client):
        """GET /api/admin/users returns all users."""
        res = admin_client.get("/api/admin/users")
        assert res.status_code == 200
        users = res.json()["users"]
        assert len(users) >= 1
        assert any(u["email"] == "admin@example.com" for u in users)

    def test_list_users_includes_is_admin(self, admin_client):
        """User dicts include is_admin field."""
        res = admin_client.get("/api/admin/users")
        users = res.json()["users"]
        admin_user = [u for u in users if u["email"] == "admin@example.com"][0]
        assert admin_user["is_admin"] is True


class TestAdminGetUser:
    def test_get_user(self, admin_client):
        """GET /api/admin/users/{id} returns a single user."""
        # Get admin's own ID
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.get(f"/api/admin/users/{me['id']}")
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "admin@example.com"

    def test_get_nonexistent_user(self, admin_client):
        res = admin_client.get("/api/admin/users/nonexistent")
        assert res.status_code == 404


class TestAdminUpdateUser:
    def test_update_tier(self, admin_client):
        """PATCH /api/admin/users/{id} can update tier."""
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"tier": "paid"},
        )
        assert res.status_code == 200
        assert res.json()["user"]["tier"] == "paid"

    def test_update_max_documents(self, admin_client):
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"max_documents": 50},
        )
        assert res.status_code == 200
        assert res.json()["user"]["max_documents"] == 50

    def test_update_max_file_size_mb(self, admin_client):
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"max_file_size_mb": 100},
        )
        assert res.status_code == 200
        assert res.json()["user"]["max_file_size_mb"] == 100

    def test_update_is_admin(self, admin_client):
        """Can promote another user to admin."""
        # Create a second user via the users module directly
        from src.web.users import create_user
        from src.web.auth import hash_password
        other = create_user(email="other@example.com", password_hash=hash_password("password123"))

        # Use admin client to promote
        res = admin_client.patch(
            f"/api/admin/users/{other.id}",
            json={"is_admin": True},
        )
        assert res.status_code == 200
        assert res.json()["user"]["is_admin"] is True

    def test_invalid_tier(self, admin_client):
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"tier": "enterprise"},
        )
        assert res.status_code == 400

    def test_invalid_max_documents(self, admin_client):
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"max_documents": -1},
        )
        assert res.status_code == 400

    def test_invalid_max_file_size_mb(self, admin_client):
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"max_file_size_mb": 0},
        )
        assert res.status_code == 400

    def test_no_valid_fields(self, admin_client):
        me = admin_client.get("/api/auth/me").json()["user"]
        res = admin_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"email": "hacker@evil.com"},
        )
        assert res.status_code == 400

    def test_nonexistent_user(self, admin_client):
        res = admin_client.patch(
            "/api/admin/users/nonexistent",
            json={"tier": "paid"},
        )
        assert res.status_code == 404

    def test_non_admin_cannot_update(self, auth_client):
        me = auth_client.get("/api/auth/me").json()["user"]
        res = auth_client.patch(
            f"/api/admin/users/{me['id']}",
            json={"tier": "paid"},
        )
        assert res.status_code == 403


class TestAdminResetUsage:
    def test_reset_usage(self, admin_client):
        """POST /api/admin/users/{id}/reset-usage resets documents_used."""
        me = admin_client.get("/api/auth/me").json()["user"]
        # Set some usage first
        update_user(me["id"], documents_used=5)

        res = admin_client.post(f"/api/admin/users/{me['id']}/reset-usage")
        assert res.status_code == 200
        assert res.json()["user"]["documents_used"] == 0

    def test_reset_nonexistent_user(self, admin_client):
        res = admin_client.post("/api/admin/users/nonexistent/reset-usage")
        assert res.status_code == 404

    def test_non_admin_cannot_reset(self, auth_client):
        me = auth_client.get("/api/auth/me").json()["user"]
        res = auth_client.post(f"/api/admin/users/{me['id']}/reset-usage")
        assert res.status_code == 403


class TestAdminStats:
    def test_stats(self, admin_client):
        """GET /api/admin/stats returns aggregate stats."""
        res = admin_client.get("/api/admin/stats")
        assert res.status_code == 200
        data = res.json()
        assert data["total_users"] >= 1
        assert "total_documents_processed" in data
        assert "users_by_tier" in data
        assert data["users_by_tier"].get("free", 0) >= 1

    def test_non_admin_cannot_see_stats(self, auth_client):
        res = auth_client.get("/api/admin/stats")
        assert res.status_code == 403


class TestForgotPassword:
    def test_forgot_returns_200_for_valid_email(self, client):
        _register(client, email="user@example.com")
        client.cookies.clear()
        res = client.post("/api/auth/forgot-password", json={"email": "user@example.com"})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_forgot_returns_200_for_nonexistent_email(self, client):
        """Returns 200 even for unknown emails to prevent email enumeration."""
        res = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_forgot_returns_400_for_missing_email(self, client):
        res = client.post("/api/auth/forgot-password", json={})
        assert res.status_code == 400

    def test_forgot_oauth_user_returns_200(self, client):
        """OAuth-only users (no password) get 200 but no email is sent."""
        from src.web.users import create_user
        create_user(email="oauth@example.com", auth_provider="google", oauth_provider_id="g123")
        res = client.post("/api/auth/forgot-password", json={"email": "oauth@example.com"})
        assert res.status_code == 200
        assert res.json()["ok"] is True


class TestResetPassword:
    def _get_reset_token(self, client, email="reset@example.com"):
        """Register a user and generate a reset token."""
        from src.web.auth import create_reset_token
        from src.web.users import get_user_by_email, set_reset_token
        from datetime import datetime, timedelta, timezone

        _register(client, email=email)
        client.cookies.clear()

        user = get_user_by_email(email)
        token = create_reset_token()
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        set_reset_token(user.id, token, expires)
        return token

    def test_reset_valid_token(self, client):
        token = self._get_reset_token(client)
        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword123",
        })
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "reset@example.com"
        assert "session" in res.cookies

    def test_reset_auto_login(self, client):
        """After reset, user is logged in (can access /me)."""
        token = self._get_reset_token(client)
        client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword123",
        })
        res = client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "reset@example.com"

    def test_reset_new_password_works(self, client):
        """After reset, can login with the new password."""
        token = self._get_reset_token(client)
        client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword123",
        })
        client.cookies.clear()
        res = client.post("/api/auth/login", json={
            "email": "reset@example.com",
            "password": "newpassword123",
        })
        assert res.status_code == 200

    def test_reset_expired_token(self, client):
        """Expired token returns 400."""
        from src.web.auth import create_reset_token
        from src.web.users import get_user_by_email, set_reset_token
        from datetime import datetime, timedelta, timezone

        _register(client, email="expired@example.com")
        client.cookies.clear()

        user = get_user_by_email("expired@example.com")
        token = create_reset_token()
        # Set expiry in the past
        expires = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        set_reset_token(user.id, token, expires)

        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword123",
        })
        assert res.status_code == 400
        assert "expired" in res.json()["error"].lower() or "invalid" in res.json()["error"].lower()

    def test_reset_invalid_token(self, client):
        res = client.post("/api/auth/reset-password", json={
            "token": "bogus-token-value",
            "password": "newpassword123",
        })
        assert res.status_code == 400

    def test_reset_short_password(self, client):
        token = self._get_reset_token(client)
        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "short",
        })
        assert res.status_code == 400
        assert "8 characters" in res.json()["error"]

    def test_reset_token_cleared_after_use(self, client):
        """Token cannot be reused after a successful reset."""
        token = self._get_reset_token(client)
        # First reset succeeds
        res1 = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword123",
        })
        assert res1.status_code == 200

        # Second reset with same token fails
        client.cookies.clear()
        res2 = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "anotherpass123",
        })
        assert res2.status_code == 400

    def test_reset_missing_token(self, client):
        res = client.post("/api/auth/reset-password", json={
            "password": "newpassword123",
        })
        assert res.status_code == 400


class TestAdminAutoPromotion:
    def test_register_promotes_admin_email(self, client, monkeypatch):
        """Registration auto-promotes if email is in ADMIN_EMAILS."""
        monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
        res = _register(client, email="boss@example.com")
        assert res.status_code == 200
        assert res.json()["user"]["is_admin"] is True

    def test_register_does_not_promote_non_admin(self, client, monkeypatch):
        """Registration doesn't promote non-admin emails."""
        monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
        res = _register(client, email="regular@example.com")
        assert res.status_code == 200
        assert res.json()["user"]["is_admin"] is False

    def test_login_promotes_admin_email(self, client, monkeypatch):
        """Login auto-promotes if email is in ADMIN_EMAILS."""
        # Register without promotion
        monkeypatch.setenv("ADMIN_EMAILS", "")
        _register(client, email="boss@example.com")
        client.cookies.clear()

        # Login with promotion enabled
        monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
        res = client.post("/api/auth/login", json={
            "email": "boss@example.com",
            "password": "password123",
        })
        assert res.status_code == 200
        assert res.json()["user"]["is_admin"] is True


# ── Helper: create a fake completed job with files on disk ──

def _create_fake_job(auth_client, tmp_path, filename="test.docx", status="completed"):
    """Create a job record with fake files for testing delete/download."""
    from src.web.jobs import create_job, update_job

    me = auth_client.get("/api/auth/me").json()["user"]

    job = create_job(filename, "", user_id=me["id"])

    # Create fake files
    original = tmp_path / f"{job.id}_{filename}"
    original.write_bytes(b"PK\x03\x04original content")

    output_dir = tmp_path / "output" / job.id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"remediated_{filename}"
    output_file.write_bytes(b"PK\x03\x04remediated content")

    report_file = output_dir / "report.html"
    report_file.write_text("<html><body>Report</body></html>")

    update_job(
        job.id,
        status=status,
        original_path=str(original),
        output_path=str(output_file),
        report_path=str(report_file),
    )

    return job


class TestDeleteJob:
    def test_delete_completed_job(self, auth_client, tmp_path):
        """DELETE /api/jobs/{id} removes a completed job."""
        job = _create_fake_job(auth_client, tmp_path)
        res = auth_client.delete(f"/api/jobs/{job.id}")
        assert res.status_code == 200
        assert res.json()["ok"] is True

        # Job should be gone
        res2 = auth_client.get(f"/api/jobs/{job.id}")
        assert res2.status_code == 404

    def test_delete_failed_job(self, auth_client, tmp_path):
        """Can delete a failed job."""
        job = _create_fake_job(auth_client, tmp_path, status="failed")
        res = auth_client.delete(f"/api/jobs/{job.id}")
        assert res.status_code == 200

    def test_delete_processing_blocked(self, auth_client, tmp_path):
        """Cannot delete a processing job — returns 409."""
        job = _create_fake_job(auth_client, tmp_path, status="processing")
        res = auth_client.delete(f"/api/jobs/{job.id}")
        assert res.status_code == 409
        assert "processing" in res.json()["error"].lower()

    def test_delete_other_users_job(self, client, tmp_path):
        """Cannot delete another user's job — returns 404."""
        # User 1 creates a job
        _register(client, email="user1@example.com")
        job = _create_fake_job(client, tmp_path)

        # User 2 tries to delete it
        client.cookies.clear()
        _register(client, email="user2@example.com")
        res = client.delete(f"/api/jobs/{job.id}")
        assert res.status_code == 404

    def test_delete_requires_auth(self, client):
        """DELETE /api/jobs/{id} requires authentication."""
        res = client.delete("/api/jobs/abc123")
        assert res.status_code == 401

    def test_delete_cleans_up_files(self, auth_client, tmp_path, monkeypatch):
        """Delete removes files from disk."""
        import src.web.app as app_mod
        monkeypatch.setattr(app_mod, "OUTPUT_DIR", tmp_path / "output")

        job = _create_fake_job(auth_client, tmp_path)
        original = Path(job.original_path) if job.original_path else None

        # Re-fetch to get updated paths
        from src.web.jobs import get_job
        job = get_job(job.id)
        output_path = Path(job.output_path)
        report_path = Path(job.report_path)

        assert output_path.exists()
        assert report_path.exists()

        auth_client.delete(f"/api/jobs/{job.id}")

        # Files should be gone
        assert not output_path.exists()
        assert not report_path.exists()

    def test_delete_graceful_if_files_gone(self, auth_client, tmp_path):
        """Delete works even if files are already removed from disk."""
        job = _create_fake_job(auth_client, tmp_path)

        # Remove files manually
        from src.web.jobs import get_job
        job = get_job(job.id)
        for p in (job.original_path, job.output_path, job.report_path):
            if p:
                Path(p).unlink(missing_ok=True)

        res = auth_client.delete(f"/api/jobs/{job.id}")
        assert res.status_code == 200


class TestBulkDelete:
    def test_bulk_delete_multiple(self, auth_client, tmp_path):
        """POST /api/jobs/bulk-delete deletes multiple jobs."""
        job1 = _create_fake_job(auth_client, tmp_path, filename="a.docx")
        job2 = _create_fake_job(auth_client, tmp_path, filename="b.docx")
        res = auth_client.post("/api/jobs/bulk-delete", json={"job_ids": [job1.id, job2.id]})
        assert res.status_code == 200
        assert res.json()["deleted"] == 2

    def test_bulk_delete_skips_processing(self, auth_client, tmp_path):
        """Bulk delete skips queued/processing jobs."""
        job1 = _create_fake_job(auth_client, tmp_path, filename="done.docx", status="completed")
        job2 = _create_fake_job(auth_client, tmp_path, filename="busy.docx", status="processing")
        res = auth_client.post("/api/jobs/bulk-delete", json={"job_ids": [job1.id, job2.id]})
        assert res.status_code == 200
        assert res.json()["deleted"] == 1

    def test_bulk_delete_empty_list(self, auth_client):
        """Empty job_ids returns 400."""
        res = auth_client.post("/api/jobs/bulk-delete", json={"job_ids": []})
        assert res.status_code == 400

    def test_bulk_delete_other_users_jobs(self, client, tmp_path):
        """Bulk delete only deletes own jobs."""
        _register(client, email="user1@example.com")
        job = _create_fake_job(client, tmp_path)

        client.cookies.clear()
        _register(client, email="user2@example.com")
        res = client.post("/api/jobs/bulk-delete", json={"job_ids": [job.id]})
        assert res.status_code == 200
        assert res.json()["deleted"] == 0

    def test_bulk_delete_requires_auth(self, client):
        res = client.post("/api/jobs/bulk-delete", json={"job_ids": ["abc"]})
        assert res.status_code == 401


class TestDownloadZip:
    def test_download_zip_multiple(self, auth_client, tmp_path):
        """POST /api/jobs/download-zip returns a ZIP file."""
        job1 = _create_fake_job(auth_client, tmp_path, filename="a.docx")
        job2 = _create_fake_job(auth_client, tmp_path, filename="b.docx")

        res = auth_client.post("/api/jobs/download-zip", json={
            "job_ids": [job1.id, job2.id],
        })
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"

        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = zf.namelist()
        assert len(names) == 2

    def test_download_zip_no_completed(self, auth_client, tmp_path):
        """ZIP with no completed jobs returns 404."""
        job = _create_fake_job(auth_client, tmp_path, status="failed")
        res = auth_client.post("/api/jobs/download-zip", json={"job_ids": [job.id]})
        assert res.status_code == 404

    def test_download_zip_duplicate_filenames(self, auth_client, tmp_path):
        """Duplicate filenames get suffixed."""
        job1 = _create_fake_job(auth_client, tmp_path, filename="same.docx")
        job2 = _create_fake_job(auth_client, tmp_path, filename="same.docx")

        res = auth_client.post("/api/jobs/download-zip", json={
            "job_ids": [job1.id, job2.id],
        })
        assert res.status_code == 200

        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(res.content))
        names = zf.namelist()
        assert len(names) == 2
        # One should have a suffix
        assert "same_1.docx" in names or "remediated_same_1.docx" in names

    def test_download_zip_requires_auth(self, client):
        res = client.post("/api/jobs/download-zip", json={"job_ids": ["abc"]})
        assert res.status_code == 401
