"""Tests for storage retention cleanup (src/web/retention.py)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.web.jobs import _local, create_job, init_db, update_job
from src.web.retention import run_cleanup
from src.web.users import init_users_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    if hasattr(_local, "conn"):
        _local.conn = None

    init_db()
    init_users_db()
    yield


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("RETENTION_ENABLED", raising=False)
    monkeypatch.delenv("RETENTION_DAYS_UPLOADS", raising=False)
    monkeypatch.delenv("RETENTION_DAYS_OUTPUT", raising=False)


@pytest.fixture
def dirs(tmp_path):
    upload = tmp_path / "uploads"
    output = tmp_path / "output"
    upload.mkdir()
    output.mkdir()
    return upload, output


def _write(path: Path, content: bytes = b"data", age_days: float = 0):
    """Write a file and set its mtime age_days in the past."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if age_days > 0:
        old = time.time() - (age_days * 86400)
        os.utime(path, (old, old))


class TestBasicCleanup:
    def test_deletes_old_uploads(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "30")

        old_file = upload / "old.pdf"
        new_file = upload / "new.pdf"
        _write(old_file, age_days=45)
        _write(new_file, age_days=5)

        report = run_cleanup(upload, output)
        assert not old_file.exists()
        assert new_file.exists()
        assert report.files_deleted == 1
        assert report.files_skipped_recent == 1

    def test_deletes_old_output(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_OUTPUT", "30")

        old_file = output / "old.pdf"
        new_file = output / "new.pdf"
        _write(old_file, age_days=60)
        _write(new_file, age_days=2)

        report = run_cleanup(upload, output)
        assert not old_file.exists()
        assert new_file.exists()
        assert report.files_deleted == 1

    def test_recurses_into_subdirs(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_OUTPUT", "30")

        old_file = output / "job_xxx" / "result.pdf"
        _write(old_file, age_days=45)

        report = run_cleanup(upload, output)
        assert not old_file.exists()
        assert report.files_deleted == 1

    def test_disabled_does_nothing(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_ENABLED", "0")
        old_file = upload / "old.pdf"
        _write(old_file, age_days=100)

        report = run_cleanup(upload, output)
        assert old_file.exists()
        assert report.files_deleted == 0

    def test_disabled_with_false(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_ENABLED", "false")
        old_file = upload / "old.pdf"
        _write(old_file, age_days=100)

        run_cleanup(upload, output)
        assert old_file.exists()


class TestActiveJobProtection:
    def test_skips_files_referenced_by_queued_job(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "30")

        active_file = upload / "active.pdf"
        _write(active_file, age_days=100)
        job = create_job(filename="active.pdf", original_path=str(active_file), user_id="u1")
        update_job(job.id, status="queued")

        report = run_cleanup(upload, output)
        assert active_file.exists()
        assert report.files_skipped_active == 1

    def test_skips_files_referenced_by_processing_job(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "30")

        active_file = upload / "active.pdf"
        _write(active_file, age_days=100)
        job = create_job(filename="active.pdf", original_path=str(active_file), user_id="u1")
        update_job(job.id, status="processing")

        run_cleanup(upload, output)
        assert active_file.exists()

    def test_deletes_files_from_completed_jobs(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "30")

        old_file = upload / "completed.pdf"
        _write(old_file, age_days=100)
        job = create_job(filename="completed.pdf", original_path=str(old_file), user_id="u1")
        update_job(job.id, status="completed")

        run_cleanup(upload, output)
        assert not old_file.exists()


class TestEnvHandling:
    def test_default_30_days(self, dirs):
        upload, output = dirs
        old_file = upload / "old.pdf"
        new_file = upload / "new.pdf"
        _write(old_file, age_days=31)
        _write(new_file, age_days=29)

        run_cleanup(upload, output)
        assert not old_file.exists()
        assert new_file.exists()

    def test_invalid_days_uses_default(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "not-a-number")
        old_file = upload / "old.pdf"
        _write(old_file, age_days=45)

        run_cleanup(upload, output)
        assert not old_file.exists()  # default 30 still applies

    def test_zero_days_uses_default(self, dirs, monkeypatch):
        # 0 isn't useful (would delete everything). Treat as default.
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "0")
        new_file = upload / "new.pdf"
        _write(new_file, age_days=5)

        run_cleanup(upload, output)
        assert new_file.exists()

    def test_custom_days(self, dirs, monkeypatch):
        upload, output = dirs
        monkeypatch.setenv("RETENTION_DAYS_UPLOADS", "7")
        old_file = upload / "old.pdf"
        new_file = upload / "new.pdf"
        _write(old_file, age_days=10)
        _write(new_file, age_days=5)

        run_cleanup(upload, output)
        assert not old_file.exists()
        assert new_file.exists()


class TestReport:
    def test_report_carries_byte_count(self, dirs):
        upload, output = dirs
        old_file = upload / "old.pdf"
        _write(old_file, content=b"x" * 1000, age_days=45)

        report = run_cleanup(upload, output)
        assert report.bytes_freed == 1000

    def test_report_has_timestamps(self, dirs):
        upload, output = dirs
        report = run_cleanup(upload, output)
        assert report.started_at
        assert report.finished_at

    def test_to_dict_has_all_keys(self, dirs):
        upload, output = dirs
        d = run_cleanup(upload, output).to_dict()
        assert set(d.keys()) == {
            "files_scanned", "files_deleted", "bytes_freed",
            "files_skipped_active", "files_skipped_recent",
            "errors", "started_at", "finished_at",
        }
