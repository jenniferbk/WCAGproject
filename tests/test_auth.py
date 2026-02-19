"""Tests for auth utilities: password hashing, JWT tokens."""

import time

import pytest

from src.web.auth import (
    create_token,
    hash_password,
    verify_password,
    verify_token,
)


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = hash_password("mysecretpassword")
        assert hashed != "mysecretpassword"
        assert verify_password("mysecretpassword", hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt auto-salts

    def test_empty_password(self):
        hashed = hash_password("")
        assert verify_password("", hashed)
        assert not verify_password("notempty", hashed)


class TestJWT:
    def test_create_and_verify_token(self):
        token = create_token("user123", "user@example.com")
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["email"] == "user@example.com"

    def test_invalid_token(self):
        assert verify_token("not.a.valid.token") is None

    def test_empty_token(self):
        assert verify_token("") is None

    def test_tampered_token(self):
        token = create_token("user1", "user1@example.com")
        tampered = token[:-4] + "XXXX"
        assert verify_token(tampered) is None

    def test_token_has_expiration(self):
        token = create_token("user1", "user1@example.com")
        payload = verify_token(token)
        assert "exp" in payload
        assert "iat" in payload
