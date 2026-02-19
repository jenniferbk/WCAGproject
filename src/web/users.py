"""User management with SQLite.

Handles user accounts, usage tracking, and tier-based limits.
Same thread-local connection pattern as jobs.py.
"""

from __future__ import annotations

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

    # Migrate: add user_id column to jobs if missing
    cursor = conn.execute("PRAGMA table_info(jobs)")
    columns = {row[1] for row in cursor.fetchall()}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)")
        conn.commit()


def _row_to_user(row) -> User:
    return User(**dict(row))


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
