"""Tests for MAX_CONCURRENT_JOBS env parsing in src/web/app.py.

The semaphore itself is created at import time, so we test the parser
function in isolation rather than re-importing the module per case.
"""

from __future__ import annotations

import pytest

from src.web.app import _read_max_concurrent_jobs


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MAX_CONCURRENT_JOBS", raising=False)


def test_default_is_one_when_unset():
    assert _read_max_concurrent_jobs() == 1


def test_default_is_one_when_empty(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "")
    assert _read_max_concurrent_jobs() == 1


def test_default_is_one_when_whitespace(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "   ")
    assert _read_max_concurrent_jobs() == 1


def test_parses_valid_int(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "3")
    assert _read_max_concurrent_jobs() == 3


def test_falls_back_on_non_integer(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "two")
    assert _read_max_concurrent_jobs() == 1


def test_falls_back_on_zero(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "0")
    assert _read_max_concurrent_jobs() == 1


def test_falls_back_on_negative(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "-5")
    assert _read_max_concurrent_jobs() == 1


def test_accepts_large_value(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "100")
    assert _read_max_concurrent_jobs() == 100
