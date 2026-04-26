"""Migrate production SQLite (data/jobs.db) to Postgres.

Usage:
    # 1. Provision Postgres + create the target DB:
    createdb a11y_remediate_prod

    # 2. Set the URL and run:
    DATABASE_URL=postgresql:///a11y_remediate_prod \\
        python -m scripts.migrate_sqlite_to_postgres \\
        --sqlite data/jobs.db \\
        [--dry-run]

The script:
1. Connects to both backends.
2. Initializes the Postgres schema via the standard init_*_db() helpers
   (so it tracks any future migrations automatically).
3. Streams rows from each SQLite table into Postgres in batches.
4. Refuses to overwrite a non-empty Postgres DB unless --force is passed.

After successful migration:
- Stop the app, set DATABASE_URL on the host, restart the app.
- Verify with `/api/admin/cost-status` (admin endpoint must respond).
- Keep the SQLite file as backup for at least one retention window.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

logger = logging.getLogger("migrate_sqlite_to_postgres")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


TABLES = ["users", "jobs", "transactions"]


def _open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"SQLite source not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _open_postgres():
    if not os.environ.get("DATABASE_URL", "").startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must be set to a Postgres URL")

    # Initialize Postgres schema using the production helpers
    from src.web.cost_cap import ensure_cost_column
    from src.web.db import get_conn, reset_thread_conn
    from src.web.jobs import init_db
    from src.web.users import init_users_db
    from src.web.billing import init_billing_db

    reset_thread_conn()
    init_db()
    init_users_db()
    init_billing_db()
    ensure_cost_column()
    return get_conn()


def _row_count(conn, table: str) -> int:
    if hasattr(conn, "dialect"):
        # Our wrapper
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def _columns_of(sqlite_conn: sqlite3.Connection, table: str) -> list[str]:
    cur = sqlite_conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    batch_size: int = 500,
    dry_run: bool = False,
) -> int:
    """Stream rows from sqlite to postgres in batches. Returns number copied."""
    src_cols = _columns_of(sqlite_conn, table)
    if not src_cols:
        logger.warning("Table %s has no columns in sqlite — skipping", table)
        return 0

    cur = sqlite_conn.execute(f"SELECT {', '.join(src_cols)} FROM {table}")
    placeholders = ", ".join(["?"] * len(src_cols))
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(src_cols)}) VALUES ({placeholders})"
    )

    total = 0
    batch: list[tuple] = []
    started = time.time()

    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            batch.append(tuple(row[c] for c in src_cols))
        if dry_run:
            total += len(batch)
            batch.clear()
            continue
        for params in batch:
            try:
                pg_conn.execute(insert_sql, params)
            except Exception as e:
                logger.error("Row insert failed in %s: %s — params=%r", table, e, params)
                pg_conn.rollback()
                raise
        pg_conn.commit()
        total += len(batch)
        batch.clear()

    elapsed = time.time() - started
    logger.info("  %s: %d rows in %.1fs%s", table, total, elapsed, " (dry-run)" if dry_run else "")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", type=Path, default=Path("data/jobs.db"),
                        help="Path to the source SQLite database (default: data/jobs.db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read sqlite + verify pg schema, but skip inserts")
    parser.add_argument("--force", action="store_true",
                        help="Allow migration into a non-empty Postgres database")
    args = parser.parse_args()

    logger.info("Source: %s", args.sqlite)
    logger.info("Target: %s", os.environ.get("DATABASE_URL"))
    logger.info("Dry run: %s", args.dry_run)

    sqlite_conn = _open_sqlite(args.sqlite)
    pg_conn = _open_postgres()

    # Safety: if any target table already has rows, refuse unless --force
    if not args.force:
        for tbl in TABLES:
            try:
                n = _row_count(pg_conn, tbl)
            except Exception:
                continue
            if n > 0:
                logger.error("Postgres table %s has %d rows — pass --force to overwrite", tbl, n)
                return 2

    grand_total = 0
    for table in TABLES:
        # Skip if table doesn't exist in sqlite (e.g., transactions on older deployments)
        try:
            sqlite_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("  %s: not present in sqlite — skipping", table)
            continue
        grand_total += _migrate_table(sqlite_conn, pg_conn, table, dry_run=args.dry_run)

    logger.info("Done: %d rows migrated%s", grand_total, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
