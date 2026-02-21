"""Tests for user CRUD operations."""

import sqlite3
import threading

import pytest

from src.web.jobs import _get_conn, _local, init_db
from src.web.users import (
    User,
    add_pages,
    create_user,
    deduct_pages,
    get_user,
    get_user_by_email,
    get_user_by_oauth,
    increment_documents_used,
    init_users_db,
    refund_pages,
    update_user,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp database for each test."""
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    # Clear thread-local connection
    if hasattr(_local, "conn"):
        _local.conn = None

    init_db()
    init_users_db()
    yield


class TestCreateUser:
    def test_create_user_basic(self):
        user = create_user(email="test@example.com", password_hash="hash123")
        assert user.email == "test@example.com"
        assert user.password_hash == "hash123"
        assert user.auth_provider == "local"
        assert user.documents_used == 0
        assert user.max_documents == 3
        assert user.max_file_size_mb == 20
        assert user.tier == "free"
        assert user.pages_balance == 20
        assert user.pages_used == 0
        assert len(user.id) == 12

    def test_create_user_with_display_name(self):
        user = create_user(email="test@example.com", display_name="Test User")
        assert user.display_name == "Test User"

    def test_create_user_oauth(self):
        user = create_user(
            email="oauth@example.com",
            auth_provider="google",
            oauth_provider_id="google-123",
            display_name="OAuth User",
        )
        assert user.auth_provider == "google"
        assert user.oauth_provider_id == "google-123"

    def test_create_duplicate_email_fails(self):
        create_user(email="dup@example.com")
        with pytest.raises(sqlite3.IntegrityError):
            create_user(email="dup@example.com")

    def test_to_dict_excludes_sensitive_fields(self):
        user = create_user(email="test@example.com", password_hash="secret_hash")
        d = user.to_dict()
        assert "password_hash" not in d
        assert "oauth_provider_id" not in d
        assert d["email"] == "test@example.com"
        assert "documents_used" in d
        assert "max_documents" in d
        assert "pages_balance" in d
        assert "pages_used" in d


class TestGetUser:
    def test_get_user_by_id(self):
        created = create_user(email="findme@example.com")
        found = get_user(created.id)
        assert found is not None
        assert found.email == "findme@example.com"

    def test_get_user_not_found(self):
        assert get_user("nonexistent") is None

    def test_get_user_by_email(self):
        create_user(email="lookup@example.com")
        found = get_user_by_email("lookup@example.com")
        assert found is not None
        assert found.email == "lookup@example.com"

    def test_get_user_by_email_case_insensitive(self):
        create_user(email="MixedCase@Example.COM")
        found = get_user_by_email("mixedcase@example.com")
        assert found is not None

    def test_get_user_by_email_not_found(self):
        assert get_user_by_email("nobody@example.com") is None

    def test_get_user_by_oauth(self):
        create_user(
            email="oauth@example.com",
            auth_provider="google",
            oauth_provider_id="g-456",
        )
        found = get_user_by_oauth("google", "g-456")
        assert found is not None
        assert found.email == "oauth@example.com"

    def test_get_user_by_oauth_not_found(self):
        assert get_user_by_oauth("google", "nonexistent") is None


class TestIncrementDocumentsUsed:
    def test_increment_under_limit(self):
        user = create_user(email="counter@example.com")
        assert user.documents_used == 0

        result = increment_documents_used(user.id)
        assert result is True

        updated = get_user(user.id)
        assert updated.documents_used == 1

    def test_increment_at_limit(self):
        user = create_user(email="full@example.com")
        # Use all 3 free documents
        for _ in range(3):
            assert increment_documents_used(user.id) is True

        # 4th should fail
        result = increment_documents_used(user.id)
        assert result is False

        updated = get_user(user.id)
        assert updated.documents_used == 3

    def test_increment_nonexistent_user(self):
        result = increment_documents_used("nonexistent")
        assert result is False


class TestUpdateUser:
    def test_update_display_name(self):
        user = create_user(email="update@example.com", display_name="Old Name")
        updated = update_user(user.id, display_name="New Name")
        assert updated.display_name == "New Name"

    def test_update_tier(self):
        user = create_user(email="upgrade@example.com")
        updated = update_user(user.id, tier="paid", max_documents=100)
        assert updated.tier == "paid"
        assert updated.max_documents == 100


class TestDeductPages:
    def test_deduct_success(self):
        user = create_user(email="pages@example.com")
        assert user.pages_balance == 20

        result = deduct_pages(user.id, 5)
        assert result is True

        updated = get_user(user.id)
        assert updated.pages_balance == 15
        assert updated.pages_used == 5
        assert updated.documents_used == 1

    def test_deduct_insufficient_balance(self):
        user = create_user(email="broke@example.com")
        # Set balance to 3
        update_user(user.id, pages_balance=3)

        result = deduct_pages(user.id, 5)
        assert result is False

        # Balance should be unchanged
        updated = get_user(user.id)
        assert updated.pages_balance == 3
        assert updated.pages_used == 0
        assert updated.documents_used == 0

    def test_deduct_exact_balance(self):
        user = create_user(email="exact@example.com")
        update_user(user.id, pages_balance=5)

        result = deduct_pages(user.id, 5)
        assert result is True

        updated = get_user(user.id)
        assert updated.pages_balance == 0
        assert updated.pages_used == 5

    def test_deduct_nonexistent_user(self):
        result = deduct_pages("nonexistent", 5)
        assert result is False

    def test_deduct_zero_balance_rejected(self):
        user = create_user(email="zero@example.com")
        update_user(user.id, pages_balance=0)

        result = deduct_pages(user.id, 1)
        assert result is False


class TestRefundPages:
    def test_refund_restores_balance(self):
        user = create_user(email="refund@example.com")
        deduct_pages(user.id, 5)

        updated = get_user(user.id)
        assert updated.pages_balance == 15
        assert updated.pages_used == 5
        assert updated.documents_used == 1

        refund_pages(user.id, 5)

        updated = get_user(user.id)
        assert updated.pages_balance == 20
        assert updated.pages_used == 0
        assert updated.documents_used == 0

    def test_refund_partial(self):
        user = create_user(email="partial@example.com")
        deduct_pages(user.id, 10)
        refund_pages(user.id, 3)

        updated = get_user(user.id)
        assert updated.pages_balance == 13
        assert updated.pages_used == 7


class TestAddPages:
    def test_add_pages(self):
        user = create_user(email="addpages@example.com")
        assert user.pages_balance == 20

        updated = add_pages(user.id, 50)
        assert updated.pages_balance == 70

    def test_add_pages_to_zero_balance(self):
        user = create_user(email="zeroadd@example.com")
        update_user(user.id, pages_balance=0)

        updated = add_pages(user.id, 100)
        assert updated.pages_balance == 100

    def test_add_pages_nonexistent_user(self):
        result = add_pages("nonexistent", 50)
        assert result is None


class TestJobsMigration:
    def test_jobs_table_has_user_id(self):
        """Verify migration added user_id column to jobs."""
        conn = _get_conn()
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "user_id" in columns

    def test_jobs_table_has_page_count(self):
        """Verify migration added page_count column to jobs."""
        conn = _get_conn()
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "page_count" in columns
