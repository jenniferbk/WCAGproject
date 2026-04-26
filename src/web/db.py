"""Database abstraction supporting SQLite (dev/tests) and Postgres (prod).

Default: SQLite at `data/jobs.db`. Existing behavior preserved for any code
path that doesn't set `DATABASE_URL`.

Postgres: set `DATABASE_URL=postgresql://user:pass@host:5432/dbname`. The
`psycopg[binary]>=3.1` dependency is optional — only required when the env
var is set. SQLite-only deployments don't need it installed.

Why a single abstraction rather than two code paths:
- Existing `?`-style parameterization works as-is (we translate to `%s` for
  Postgres at execute time).
- `Row` supports both positional access (`row[0]`) and key access
  (`row['column_name']`) on both backends, mirroring SQLite's `Row` so no
  caller has to change.
- Schema differences (PRAGMA vs information_schema, ALTER TABLE syntax) are
  encapsulated in helpers like `column_exists()`.

Connections are thread-local: each thread gets its own connection (matches
the prior `threading.local` pattern in `src/web/jobs.py`).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Default SQLite path. Tests and code paths that previously imported
# `src.web.jobs.DB_PATH` still work — that module re-exports this constant
# and uses our abstraction internally.
_DEFAULT_SQLITE_PATH = Path(__file__).parent.parent.parent / "data" / "jobs.db"

_local = threading.local()


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def _is_postgres() -> bool:
    url = _database_url()
    return url.startswith("postgres://") or url.startswith("postgresql://")


def get_dialect() -> str:
    """Return 'postgres' or 'sqlite' based on current env."""
    return "postgres" if _is_postgres() else "sqlite"


# ── Row that supports both positional and named access ────────────
class Row(dict):
    """dict that also supports positional access via insertion order.

    Mirrors `sqlite3.Row` semantics so callers can use either style.
    """

    def __getitem__(self, key):
        if isinstance(key, int):
            try:
                return list(self.values())[key]
            except IndexError:
                raise IndexError(f"Row index {key} out of range")
        return super().__getitem__(key)


# ── Cursor + Connection wrappers ──────────────────────────────────
class _CursorWrapper:
    """Wraps a SQLite or psycopg3 cursor to normalize fetchone/fetchall to Row."""

    def __init__(self, cur, dialect: str):
        self._cur = cur
        self._dialect = dialect

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self):
        # psycopg3 doesn't expose lastrowid; callers needing this must use
        # RETURNING. SQLite supports it natively.
        return getattr(self._cur, "lastrowid", None)

    def _column_names(self) -> list[str]:
        if self._dialect == "sqlite":
            return [d[0] for d in (self._cur.description or [])]
        # psycopg3: cur.description is list of Column objects with .name
        return [d.name if hasattr(d, "name") else d[0] for d in (self._cur.description or [])]

    def _wrap(self, raw):
        if raw is None:
            return None
        cols = self._column_names()
        if isinstance(raw, dict):
            return Row(raw)
        # tuple-like (sqlite3 returns Row which is tuple-indexable; psycopg3 returns tuple by default)
        return Row(zip(cols, raw))

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    def close(self):
        self._cur.close()


def _translate_qmark(sql: str) -> str:
    """Convert SQLite-style `?` placeholders to psycopg3 `%s` placeholders.

    Walks the string respecting single-quoted strings so `?` inside literals
    isn't touched. Doesn't try to handle every edge case (no support for
    `--` line comments etc.) — sufficient for our SQL.
    """
    out = []
    in_squote = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "'":
            in_squote = not in_squote
            out.append(c)
        elif c == "?" and not in_squote:
            out.append("%s")
        else:
            out.append(c)
        i += 1
    return "".join(out)


class Connection:
    """Thin wrapper that exposes the parts of DB-API 2.0 we use, normalized.

    `conn.execute(sql, params)` returns a `_CursorWrapper`. The SQL is in
    SQLite `?` style; we translate to `%s` automatically for Postgres.
    """

    def __init__(self, raw, dialect: str):
        self._raw = raw
        self._dialect = dialect

    @property
    def dialect(self) -> str:
        return self._dialect

    def execute(self, sql: str, params: tuple | list | None = None):
        cur = self._raw.cursor() if self._dialect == "postgres" else self._raw
        if self._dialect == "postgres":
            cur.execute(_translate_qmark(sql), params or ())
            return _CursorWrapper(cur, self._dialect)
        # SQLite: connection.execute returns a cursor directly
        sqlite_cur = self._raw.execute(sql, params or ())
        return _CursorWrapper(sqlite_cur, self._dialect)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        finally:
            pass


def _connect_postgres() -> Connection:
    try:
        import psycopg
        from psycopg.rows import tuple_row
    except ImportError as e:
        raise RuntimeError(
            "DATABASE_URL is set to a Postgres URL but psycopg is not installed. "
            "Install with `pip install 'psycopg[binary]>=3.1'` or unset DATABASE_URL "
            "to fall back to SQLite."
        ) from e
    raw = psycopg.connect(_database_url(), row_factory=tuple_row, autocommit=False)
    return Connection(raw, "postgres")


def _connect_sqlite(path: Path | None = None) -> Connection:
    db_path = path or _DEFAULT_SQLITE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    return Connection(raw, "sqlite")


def get_conn(sqlite_path: Path | None = None) -> Connection:
    """Return the thread-local connection, creating it on first call.

    If `DATABASE_URL` is set to a Postgres URL, uses Postgres. Otherwise,
    uses SQLite at `sqlite_path` or the default location.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        if _is_postgres():
            _local.conn = _connect_postgres()
        else:
            _local.conn = _connect_sqlite(sqlite_path)
    return _local.conn


def reset_thread_conn() -> None:
    """Drop the thread-local connection. Tests use this between cases.

    Public alternative to poking `_local.conn = None` directly.
    """
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


# ── Schema helpers (encapsulate dialect differences) ──────────────
def column_exists(conn: Connection, table: str, column: str) -> bool:
    """Backend-agnostic existence check."""
    if conn.dialect == "sqlite":
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in rows)
    rows = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        (table, column),
    ).fetchall()
    return len(rows) > 0


def table_columns(conn: Connection, table: str) -> set[str]:
    """Return the set of column names for a table."""
    if conn.dialect == "sqlite":
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        (table,),
    ).fetchall()
    return {r[0] for r in rows}


def is_integrity_error(exc: BaseException) -> bool:
    """True if `exc` is a DB-API IntegrityError from sqlite3 or psycopg.

    Both drivers expose `IntegrityError` per PEP 249. This helper avoids
    forcing callers to import the right one — `except Exception` + this
    check is sufficient.
    """
    return type(exc).__name__ == "IntegrityError"


def begin_immediate(conn: Connection) -> None:
    """Open a transaction with serialized write semantics.

    SQLite: `BEGIN IMMEDIATE` reserves the DB so no other writer can start.
    Postgres: a regular `BEGIN` is sufficient since MVCC + row locks
    handle concurrent writes via the engine.
    """
    if conn.dialect == "sqlite":
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("BEGIN")
