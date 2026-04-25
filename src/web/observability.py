"""Request-scoped observability: request IDs and structured-log enrichment.

Adds a UUID4 to every inbound HTTP request (or accepts X-Request-ID from a
trusted upstream like Caddy if present), exposes it via the response header,
and threads it into every log record made during that request via a
ContextVar-backed log filter. Lets you grep one request across user → job →
API calls in journalctl.

Usage:
    from src.web.observability import (
        RequestIdMiddleware,
        configure_logging,
    )
    app.add_middleware(RequestIdMiddleware)
    configure_logging()
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Empty when outside a request context
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdFilter(logging.Filter):
    """Injects request_id from the ContextVar into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO, fmt: str = _DEFAULT_FORMAT) -> None:
    """Idempotent: install our format + filter on the root logger."""
    root = logging.getLogger()
    # Don't double-install on reload
    for h in root.handlers:
        if getattr(h, "_a11y_request_id_configured", False):
            return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(RequestIdFilter())
    handler._a11y_request_id_configured = True  # type: ignore[attr-defined]

    root.addHandler(handler)
    root.setLevel(level)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generates or accepts X-Request-ID, threads it via ContextVar, returns it.

    Trusts an upstream X-Request-ID if present (so Caddy can correlate its
    own logs with ours). Otherwise generates UUID4. Always echoed in the
    response header so clients can quote it in support tickets.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id", "").strip()
        # Accept reasonable upstream IDs but cap length
        if incoming and len(incoming) <= 128 and all(c.isprintable() for c in incoming):
            req_id = incoming
        else:
            req_id = uuid.uuid4().hex

        token = request_id_var.set(req_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = req_id
        return response
