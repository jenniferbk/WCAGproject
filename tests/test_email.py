"""Tests for email notification module."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from src.web import email as email_mod


@dataclass
class FakeJob:
    id: str = "abc123"
    filename: str = "test.docx"
    status: str = "completed"
    issues_before: int = 10
    issues_after: int = 3
    issues_fixed: int = 7
    human_review_count: int = 2
    processing_time: float = 45.3
    error: str = ""
    user_id: str = "user1"


class TestIsConfigured:
    def test_not_configured_by_default(self):
        with patch.object(email_mod, "SMTP_HOST", ""), patch.object(email_mod, "SMTP_FROM", ""):
            assert email_mod._is_configured() is False

    def test_configured_when_host_and_from_set(self):
        with patch.object(email_mod, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_mod, "SMTP_FROM", "noreply@example.com"):
            assert email_mod._is_configured() is True

    def test_not_configured_missing_from(self):
        with patch.object(email_mod, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_mod, "SMTP_FROM", ""):
            assert email_mod._is_configured() is False

    def test_not_configured_missing_host(self):
        with patch.object(email_mod, "SMTP_HOST", ""), \
             patch.object(email_mod, "SMTP_FROM", "noreply@example.com"):
            assert email_mod._is_configured() is False


class TestSend:
    def test_skips_when_not_configured(self):
        with patch.object(email_mod, "SMTP_HOST", ""), patch.object(email_mod, "SMTP_FROM", ""):
            result = email_mod._send("user@example.com", "Test", "<p>Hi</p>")
            assert result is False

    @patch("src.web.email.smtplib.SMTP")
    def test_sends_email_when_configured(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(email_mod, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_mod, "SMTP_FROM", "noreply@example.com"), \
             patch.object(email_mod, "SMTP_USER", "user"), \
             patch.object(email_mod, "SMTP_PASSWORD", "pass"):
            result = email_mod._send("recipient@example.com", "Subject", "<p>Body</p>")

        assert result is True
        mock_server.ehlo.assert_called()
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")
        mock_server.sendmail.assert_called_once()
        args = mock_server.sendmail.call_args[0]
        assert args[0] == "noreply@example.com"
        assert args[1] == ["recipient@example.com"]

    @patch("src.web.email.smtplib.SMTP")
    def test_sends_without_auth(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(email_mod, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_mod, "SMTP_FROM", "noreply@example.com"), \
             patch.object(email_mod, "SMTP_USER", ""), \
             patch.object(email_mod, "SMTP_PASSWORD", ""):
            result = email_mod._send("recipient@example.com", "Subject", "<p>Body</p>")

        assert result is True
        mock_server.login.assert_not_called()

    @patch("src.web.email.smtplib.SMTP")
    def test_handles_smtp_error_gracefully(self, mock_smtp_class):
        mock_smtp_class.side_effect = ConnectionRefusedError("Connection refused")

        with patch.object(email_mod, "SMTP_HOST", "smtp.example.com"), \
             patch.object(email_mod, "SMTP_FROM", "noreply@example.com"):
            result = email_mod._send("recipient@example.com", "Subject", "<p>Body</p>")

        assert result is False


class TestSendJobCompleteEmail:
    @patch("src.web.email._send")
    def test_sends_complete_email(self, mock_send):
        mock_send.return_value = True
        job = FakeJob()
        result = email_mod.send_job_complete_email("user@example.com", job)

        assert result is True
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert args[0] == "user@example.com"
        assert "test.docx" in args[1]  # subject
        assert "Remediation Complete" in args[2]  # html body
        assert "7" in args[2]  # issues_fixed

    @patch("src.web.email._send")
    def test_complete_email_html_escapes(self, mock_send):
        mock_send.return_value = True
        job = FakeJob(filename="<script>alert(1)</script>.docx")
        email_mod.send_job_complete_email("user@example.com", job)

        html = mock_send.call_args[0][2]
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestSendJobFailedEmail:
    @patch("src.web.email._send")
    def test_sends_failed_email(self, mock_send):
        mock_send.return_value = True
        job = FakeJob(status="failed", error="Parse error: corrupted file")
        result = email_mod.send_job_failed_email("user@example.com", job)

        assert result is True
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert "failed" in args[1].lower()
        assert "Remediation Failed" in args[2]
        assert "Parse error" in args[2]

    @patch("src.web.email._send")
    def test_truncates_long_error(self, mock_send):
        mock_send.return_value = True
        job = FakeJob(status="failed", error="x" * 500)
        email_mod.send_job_failed_email("user@example.com", job)

        html = mock_send.call_args[0][2]
        # Error should be truncated to 200 chars max
        assert "x" * 201 not in html


class TestSendPasswordResetEmail:
    @patch("src.web.email._send")
    def test_sends_reset_email(self, mock_send):
        mock_send.return_value = True
        result = email_mod.send_password_reset_email(
            "user@example.com",
            "http://localhost:8000/?reset=abc123token",
        )

        assert result is True
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert args[0] == "user@example.com"
        assert "Reset" in args[1] or "reset" in args[1]  # subject
        assert "http://localhost:8000/?reset=abc123token" in args[2]  # html body
        assert "1 hour" in args[2]

    @patch("src.web.email._send")
    def test_reset_email_html_escapes(self, mock_send):
        mock_send.return_value = True
        email_mod.send_password_reset_email(
            "user@example.com",
            'http://example.com/?reset=<script>alert("xss")</script>',
        )

        html = mock_send.call_args[0][2]
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestEsc:
    def test_escapes_html_entities(self):
        assert email_mod._esc('<script>alert("xss")</script>') == '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'

    def test_escapes_ampersand(self):
        assert email_mod._esc("a&b") == "a&amp;b"

    def test_plain_text_unchanged(self):
        assert email_mod._esc("hello world") == "hello world"
