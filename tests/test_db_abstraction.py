"""Unit tests for the db.py SQLite/Postgres abstraction layer.

Postgres path requires `psycopg[binary]` and a running Postgres — those
tests are gated on `DATABASE_URL` and skipped when not available, so the
suite still runs anywhere. SQLite path is tested unconditionally.
"""

from __future__ import annotations

import os

import pytest

from src.web.db import (
    Row,
    _translate_qmark,
    column_exists,
    get_conn,
    get_dialect,
    is_integrity_error,
    reset_thread_conn,
    table_columns,
)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Point sqlite at temp dir
    import src.web.db as db_mod
    monkeypatch.setattr(db_mod, "_DEFAULT_SQLITE_PATH", tmp_path / "test.db")
    reset_thread_conn()
    yield
    reset_thread_conn()


class TestRow:
    def test_positional_access(self):
        r = Row({"a": 1, "b": 2, "c": 3})
        assert r[0] == 1
        assert r[1] == 2
        assert r[2] == 3

    def test_named_access(self):
        r = Row({"a": 1, "b": 2})
        assert r["a"] == 1
        assert r["b"] == 2

    def test_dict_conversion(self):
        r = Row({"a": 1, "b": 2})
        assert dict(r) == {"a": 1, "b": 2}

    def test_iteration_keys(self):
        r = Row({"a": 1, "b": 2})
        assert list(r) == ["a", "b"]

    def test_index_out_of_range(self):
        r = Row({"a": 1})
        with pytest.raises(IndexError):
            _ = r[5]


class TestTranslateQmark:
    def test_simple(self):
        assert _translate_qmark("SELECT * FROM t WHERE a = ?") == "SELECT * FROM t WHERE a = %s"

    def test_multiple(self):
        assert _translate_qmark("INSERT INTO t VALUES (?, ?, ?)") == "INSERT INTO t VALUES (%s, %s, %s)"

    def test_preserves_quoted_qmark(self):
        # ? inside a single-quoted literal must NOT be translated
        sql = "SELECT * FROM t WHERE name = ? AND label = '?'"
        assert _translate_qmark(sql) == "SELECT * FROM t WHERE name = %s AND label = '?'"

    def test_no_placeholders(self):
        assert _translate_qmark("SELECT 1") == "SELECT 1"


class TestDialectDetection:
    def test_default_is_sqlite(self):
        assert get_dialect() == "sqlite"

    def test_postgres_url_detected(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        assert get_dialect() == "postgres"

    def test_postgres_legacy_url_detected(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@localhost/db")
        assert get_dialect() == "postgres"

    def test_empty_url_is_sqlite(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        assert get_dialect() == "sqlite"


class TestSqliteConn:
    def test_connect_creates_db(self):
        conn = get_conn()
        assert conn.dialect == "sqlite"

    def test_execute_returns_wrapped_cursor(self):
        conn = get_conn()
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO t VALUES (?, ?)", (1, "hello"))
        conn.commit()

        cur = conn.execute("SELECT * FROM t WHERE id = ?", (1,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 1
        assert row["name"] == "hello"

    def test_fetchall_returns_rows(self):
        conn = get_conn()
        conn.execute("CREATE TABLE t (n INTEGER)")
        for i in range(3):
            conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.commit()

        rows = conn.execute("SELECT n FROM t ORDER BY n").fetchall()
        assert len(rows) == 3
        assert [r[0] for r in rows] == [0, 1, 2]
        assert [r["n"] for r in rows] == [0, 1, 2]

    def test_fetchone_empty_returns_none(self):
        conn = get_conn()
        conn.execute("CREATE TABLE t (n INTEGER)")
        conn.commit()
        assert conn.execute("SELECT * FROM t").fetchone() is None


class TestSchemaHelpers:
    def test_column_exists_true(self):
        conn = get_conn()
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.commit()
        assert column_exists(conn, "t", "a") is True
        assert column_exists(conn, "t", "b") is True

    def test_column_exists_false(self):
        conn = get_conn()
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.commit()
        assert column_exists(conn, "t", "missing") is False

    def test_table_columns(self):
        conn = get_conn()
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT, c REAL)")
        conn.commit()
        cols = table_columns(conn, "t")
        assert cols == {"a", "b", "c"}


class TestIsIntegrityError:
    def test_sqlite_integrity_error_detected(self):
        import sqlite3
        try:
            raise sqlite3.IntegrityError("UNIQUE constraint failed")
        except Exception as e:
            assert is_integrity_error(e)

    def test_random_exception_not_integrity_error(self):
        try:
            raise ValueError("nope")
        except Exception as e:
            assert not is_integrity_error(e)

    def test_runtime_error_not_integrity_error(self):
        try:
            raise RuntimeError("nope")
        except Exception as e:
            assert not is_integrity_error(e)


class TestThreadLocal:
    def test_same_thread_same_conn(self):
        c1 = get_conn()
        c2 = get_conn()
        assert c1 is c2

    def test_reset_creates_new_conn(self):
        c1 = get_conn()
        reset_thread_conn()
        c2 = get_conn()
        assert c1 is not c2
