"""Email notifications for job completion/failure.

Uses stdlib smtplib + email. Silently skips if SMTP is not configured.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")


def _is_configured() -> bool:
    """Return True if SMTP settings are present."""
    return bool(SMTP_HOST and SMTP_FROM)


def _send(to: str, subject: str, html: str) -> bool:
    """Send an email. Returns True on success, False on failure."""
    if not _is_configured():
        logger.debug("SMTP not configured, skipping email to %s", to)
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to], msg.as_string())
        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def send_job_complete_email(email: str, job) -> bool:
    """Send a completion notification email."""
    subject = f"Remediation complete: {job.filename}"
    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;color:#1a1d23">
  <h2 style="color:#16a34a">Remediation Complete</h2>
  <p><strong>{_esc(job.filename)}</strong> has been remediated.</p>
  <table style="border-collapse:collapse;margin:1rem 0;font-size:0.9rem">
    <tr><td style="padding:0.3rem 1rem 0.3rem 0;color:#5f6672">Issues fixed</td><td><strong>{job.issues_fixed}</strong></td></tr>
    <tr><td style="padding:0.3rem 1rem 0.3rem 0;color:#5f6672">Remaining</td><td><strong>{job.issues_after}</strong></td></tr>
    <tr><td style="padding:0.3rem 1rem 0.3rem 0;color:#5f6672">Needs review</td><td><strong>{job.human_review_count}</strong></td></tr>
    <tr><td style="padding:0.3rem 1rem 0.3rem 0;color:#5f6672">Processing time</td><td>{round(job.processing_time, 1)}s</td></tr>
  </table>
  <p><a href="{SITE_URL}" style="color:#2563eb">Open A11y Remediation</a> to download your file and view the report.</p>
</div>"""
    return _send(email, subject, html)


def send_job_failed_email(email: str, job) -> bool:
    """Send a failure notification email."""
    error_snippet = (job.error or "Unknown error")[:200]
    subject = f"Remediation failed: {job.filename}"
    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;color:#1a1d23">
  <h2 style="color:#dc2626">Remediation Failed</h2>
  <p><strong>{_esc(job.filename)}</strong> could not be remediated.</p>
  <p style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:0.75rem;font-size:0.85rem;color:#991b1b">{_esc(error_snippet)}</p>
  <p>You can try uploading the file again or <a href="{SITE_URL}" style="color:#2563eb">contact support</a>.</p>
</div>"""
    return _send(email, subject, html)


def _esc(s: str) -> str:
    """Minimal HTML escaping."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
