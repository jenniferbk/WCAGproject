"""Postgres-backend smoke tests.

Skipped when `DATABASE_URL` is not set or psycopg is not installed. Run
locally with:
    createdb a11y_remediate_test
    DATABASE_URL=postgresql:///a11y_remediate_test pytest tests/test_postgres_smoke.py -v

Verifies that the schema, migrations, and core CRUD round-trip work on
Postgres. Broader tests (test_billing.py, test_users.py, etc.) run on
SQLite because they rely on per-test SQLite-file isolation; the
abstraction layer in src/web/db.py is what guarantees behavior parity,
and these smoke tests catch the few places where Postgres differs.
"""

from __future__ import annotations

import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_POSTGRES = DATABASE_URL.startswith(("postgresql://", "postgres://"))

if not IS_POSTGRES:
    pytest.skip(
        "DATABASE_URL is not set to a Postgres URL — run "
        "`DATABASE_URL=postgresql:///a11y_remediate_test pytest tests/test_postgres_smoke.py` "
        "to enable.",
        allow_module_level=True,
    )

try:
    import psycopg  # noqa: F401
except ImportError:
    pytest.skip("psycopg not installed", allow_module_level=True)

from src.web.cost_cap import ensure_cost_column, record_job_cost
from src.web.db import column_exists, get_conn, reset_thread_conn
from src.web.jobs import create_job, get_job, init_db, list_jobs, update_job
from src.web.users import create_user, get_user_by_email, init_users_db


def _drop_all_tables():
    """Per-test cleanup — drop and recreate all tables."""
    reset_thread_conn()
    conn = get_conn()
    for tbl in ("transactions", "jobs", "users"):
        conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
    conn.commit()


@pytest.fixture(autouse=True)
def fresh_pg():
    _drop_all_tables()
    init_db()
    init_users_db()
    ensure_cost_column()
    yield
    _drop_all_tables()


class TestSchemaCreation:
    def test_jobs_table_exists(self):
        conn = get_conn()
        rows = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            ("jobs",),
        ).fetchall()
        assert len(rows) == 1

    def test_users_table_exists(self):
        conn = get_conn()
        rows = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            ("users",),
        ).fetchall()
        assert len(rows) == 1

    def test_jobs_has_estimated_cost_column(self):
        conn = get_conn()
        assert column_exists(conn, "jobs", "estimated_cost_usd")

    def test_users_has_pages_balance(self):
        conn = get_conn()
        assert column_exists(conn, "users", "pages_balance")


class TestJobsRoundtrip:
    def test_create_get_update_job(self):
        job = create_job(
            filename="test.pdf",
            original_path="/tmp/test.pdf",
            user_id="u1",
        )
        assert job.id
        assert job.filename == "test.pdf"
        assert job.status == "queued"

        update_job(job.id, status="completed", issues_fixed=5)
        fetched = get_job(job.id)
        assert fetched.status == "completed"
        assert fetched.issues_fixed == 5

    def test_list_jobs_filters_by_user(self):
        create_job(filename="a.pdf", original_path="/tmp/a.pdf", user_id="ua")
        create_job(filename="b.pdf", original_path="/tmp/b.pdf", user_id="ub")
        ja = list_jobs(user_id="ua")
        jb = list_jobs(user_id="ub")
        assert len(ja) == 1 and ja[0].filename == "a.pdf"
        assert len(jb) == 1 and jb[0].filename == "b.pdf"


class TestUsersRoundtrip:
    def test_create_and_fetch_user(self):
        u = create_user(email="pg@example.com", password_hash="h", display_name="PG")
        assert u.id
        fetched = get_user_by_email("pg@example.com")
        assert fetched is not None
        assert fetched.id == u.id
        assert fetched.is_admin is False  # bool conversion from INTEGER

    def test_email_case_insensitive_lookup(self):
        create_user(email="PG@Example.com", password_hash="h")
        u = get_user_by_email("pg@example.com")
        assert u is not None


class TestCostColumnRoundtrip:
    def test_record_and_query_cost(self):
        job = create_job(filename="x.pdf", original_path="/tmp/x.pdf", user_id="u1")
        record_job_cost(job.id, 0.42)

        conn = get_conn()
        row = conn.execute(
            "SELECT estimated_cost_usd FROM jobs WHERE id = ?",
            (job.id,),
        ).fetchone()
        assert float(row[0]) == pytest.approx(0.42)


class TestBeginImmediate:
    def test_begin_immediate_no_error(self):
        from src.web.db import begin_immediate

        conn = get_conn()
        # On Postgres this issues a plain BEGIN — should not error
        begin_immediate(conn)
        conn.execute("SELECT 1")
        conn.rollback()
