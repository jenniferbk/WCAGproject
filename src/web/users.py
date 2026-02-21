"""User management with SQLite.

Handles user accounts, usage tracking, and tier-based limits.
Same thread-local connection pattern as jobs.py.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.web.jobs import _get_conn


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    display_name: str
    auth_provider: str  # 'local', 'google', 'microsoft'
    oauth_provider_id: str
    documents_used: int
    max_documents: int
    max_file_size_mb: int
    tier: str  # 'free', 'paid'
    created_at: str
    updated_at: str
    is_admin: bool = False
    pages_balance: int = 20
    pages_used: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "auth_provider": self.auth_provider,
            "documents_used": self.documents_used,
            "max_documents": self.max_documents,
            "max_file_size_mb": self.max_file_size_mb,
            "tier": self.tier,
            "created_at": self.created_at,
            "is_admin": self.is_admin,
            "pages_balance": self.pages_balance,
            "pages_used": self.pages_used,
        }


def init_users_db() -> None:
    """Create the users table and migrate jobs table if needed."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            auth_provider TEXT DEFAULT 'local',
            oauth_provider_id TEXT DEFAULT '',
            documents_used INTEGER DEFAULT 0,
            max_documents INTEGER DEFAULT 3,
            max_file_size_mb INTEGER DEFAULT 20,
            tier TEXT DEFAULT 'free',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # Migrate: add missing columns
    cursor = conn.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if "is_admin" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        conn.commit()
    if "password_reset_token" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_reset_token TEXT DEFAULT ''")
        conn.commit()
    if "password_reset_expires" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_reset_expires TEXT DEFAULT ''")
        conn.commit()
    if "pages_balance" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN pages_balance INTEGER DEFAULT 20")
        conn.commit()
    if "pages_used" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN pages_used INTEGER DEFAULT 0")
        conn.commit()

    # Migrate: add columns to jobs if missing
    cursor = conn.execute("PRAGMA table_info(jobs)")
    columns = {row[1] for row in cursor.fetchall()}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)")
        conn.commit()
    if "batch_id" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch_id ON jobs(batch_id)")
        conn.commit()
    if "phase" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN phase TEXT DEFAULT ''")
        conn.commit()
    if "companion_path" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN companion_path TEXT DEFAULT ''")
        conn.commit()
    if "page_count" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN page_count INTEGER DEFAULT 0")
        conn.commit()
    if "started_at" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN started_at TEXT DEFAULT ''")
        conn.commit()
    if "phase_detail" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN phase_detail TEXT DEFAULT ''")
        conn.commit()


def _row_to_user(row) -> User:
    d = dict(row)
    d["is_admin"] = bool(d.get("is_admin", 0))
    d.setdefault("pages_balance", 20)
    d.setdefault("pages_used", 0)
    # Remove fields not in the User dataclass
    d.pop("password_reset_token", None)
    d.pop("password_reset_expires", None)
    return User(**d)


def create_user(
    email: str,
    password_hash: str = "",
    display_name: str = "",
    auth_provider: str = "local",
    oauth_provider_id: str = "",
) -> User:
    """Create a new user. Returns the created User."""
    conn = _get_conn()
    user_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO users (id, email, password_hash, display_name, auth_provider,
           oauth_provider_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, email, password_hash, display_name, auth_provider, oauth_provider_id, now, now),
    )
    conn.commit()
    return get_user(user_id)  # type: ignore[return-value]


def get_user(user_id: str) -> User | None:
    """Get a user by ID."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(email: str) -> User | None:
    """Get a user by email (case-insensitive)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email,)
    ).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_oauth(provider: str, provider_id: str) -> User | None:
    """Get a user by OAuth provider + provider ID."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE auth_provider = ? AND oauth_provider_id = ?",
        (provider, provider_id),
    ).fetchone()
    return _row_to_user(row) if row else None


def increment_documents_used(user_id: str) -> bool:
    """Atomically increment documents_used if under limit.

    Returns True if incremented, False if limit reached.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """UPDATE users SET documents_used = documents_used + 1, updated_at = ?
           WHERE id = ? AND documents_used < max_documents""",
        (now, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def update_user(user_id: str, **kwargs) -> User | None:
    """Update user fields."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    kwargs["updated_at"] = now

    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn.execute(f"UPDATE users SET {sets} WHERE id = ?", values)
    conn.commit()
    return get_user(user_id)


def list_users() -> list[User]:
    """Return all users, newest first."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_row_to_user(row) for row in rows]


def reset_documents_used(user_id: str) -> User | None:
    """Reset documents_used to 0 for a user."""
    return update_user(user_id, documents_used=0)


def _hash_token(token: str) -> str:
    """SHA-256 hash a reset token for safe DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def set_reset_token(user_id: str, token: str, expires_at: str) -> None:
    """Store a hashed password reset token and expiry for a user."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE users SET password_reset_token = ?, password_reset_expires = ?, updated_at = ? WHERE id = ?",
        (_hash_token(token), expires_at, now, user_id),
    )
    conn.commit()


def get_user_by_reset_token(token: str) -> User | None:
    """Find a user with a matching, non-expired reset token."""
    conn = _get_conn()
    hashed = _hash_token(token)
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT * FROM users WHERE password_reset_token = ? AND password_reset_expires > ?",
        (hashed, now),
    ).fetchone()
    return _row_to_user(row) if row else None


def clear_reset_token(user_id: str) -> None:
    """Clear a user's password reset token and expiry."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE users SET password_reset_token = '', password_reset_expires = '', updated_at = ? WHERE id = ?",
        (now, user_id),
    )
    conn.commit()


def deduct_pages(user_id: str, page_count: int) -> bool:
    """Atomically deduct pages from a user's balance.

    Returns True if deducted, False if insufficient balance.
    Also increments documents_used and pages_used for stats.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """UPDATE users
           SET pages_balance = pages_balance - ?,
               pages_used = pages_used + ?,
               documents_used = documents_used + 1,
               updated_at = ?
           WHERE id = ? AND pages_balance >= ?""",
        (page_count, page_count, now, user_id, page_count),
    )
    conn.commit()
    return cursor.rowcount > 0


def refund_pages(user_id: str, page_count: int) -> None:
    """Reverse a page deduction on job failure."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE users
           SET pages_balance = pages_balance + ?,
               pages_used = pages_used - ?,
               documents_used = MAX(documents_used - 1, 0),
               updated_at = ?
           WHERE id = ?""",
        (page_count, page_count, now, user_id),
    )
    conn.commit()


def add_pages(user_id: str, page_count: int) -> User | None:
    """Add pages to a user's balance (admin use)."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE users SET pages_balance = pages_balance + ?, updated_at = ? WHERE id = ?",
        (page_count, now, user_id),
    )
    conn.commit()
    return get_user(user_id)
