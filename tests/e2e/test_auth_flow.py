"""E2E: authentication flows — register, login, logout, /me, password reset."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestRegistration:
    def test_register_creates_account_and_session(self, client):
        res = client.post("/api/auth/register", json={
            "email": "new@example.com",
            "password": "validpass123",
            "display_name": "New User",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["display_name"] == "New User"
        assert "session" in res.cookies

    def test_register_duplicate_email_fails(self, client):
        client.post("/api/auth/register", json={
            "email": "dup@example.com", "password": "validpass123",
        })
        res = client.post("/api/auth/register", json={
            "email": "dup@example.com", "password": "validpass123",
        })
        assert res.status_code in (400, 409)

    def test_register_short_password_fails(self, client):
        res = client.post("/api/auth/register", json={
            "email": "weak@example.com", "password": "short",
        })
        assert res.status_code == 400


class TestLoginLogout:
    def test_login_and_logout(self, client):
        client.post("/api/auth/register", json={
            "email": "logme@example.com", "password": "validpass123",
        })
        # Logout to drop the session
        client.post("/api/auth/logout")

        res = client.post("/api/auth/login", json={
            "email": "logme@example.com", "password": "validpass123",
        })
        assert res.status_code == 200
        assert "session" in res.cookies

        res2 = client.post("/api/auth/logout")
        assert res2.status_code == 200

    def test_login_wrong_password_rejected(self, client):
        client.post("/api/auth/register", json={
            "email": "victim@example.com", "password": "validpass123",
        })
        client.post("/api/auth/logout")
        res = client.post("/api/auth/login", json={
            "email": "victim@example.com", "password": "wrongpass",
        })
        assert res.status_code == 401

    def test_login_unknown_user_rejected(self, client):
        res = client.post("/api/auth/login", json={
            "email": "ghost@example.com", "password": "validpass123",
        })
        assert res.status_code == 401


class TestMeEndpoint:
    def test_me_returns_authenticated_user(self, auth_client):
        res = auth_client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "user@example.com"

    def test_me_unauthenticated_rejected(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401


class TestPasswordReset:
    def test_forgot_password_returns_success_for_unknown_email(self, client):
        # Doesn't reveal whether the email is registered (anti-enumeration)
        res = client.post("/api/auth/forgot-password", json={
            "email": "ghost@example.com",
        })
        assert res.status_code in (200, 204)

    def test_reset_password_with_invalid_token_rejected(self, client):
        res = client.post("/api/auth/reset-password", json={
            "token": "garbage",
            "password": "newvalidpass123",
        })
        assert res.status_code in (400, 401, 404)
