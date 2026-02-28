"""In-memory sliding window rate limiter for FastAPI endpoints."""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    """Sliding window rate limiter using in-memory timestamp lists.

    Thread-safe. Periodically prunes stale keys to prevent memory growth.
    """

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300  # 5 minutes

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """Check if a request is allowed under the rate limit.

        Returns (allowed, retry_after_seconds). retry_after is 0 if allowed.
        """
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            # Prune expired timestamps for this key
            timestamps = self._windows[key]
            self._windows[key] = [t for t in timestamps if t > cutoff]
            timestamps = self._windows[key]

            if len(timestamps) >= max_requests:
                # Calculate when the oldest request in the window expires
                retry_after = int(timestamps[0] - cutoff) + 1
                return False, max(retry_after, 1)

            timestamps.append(now)

            # Periodic cleanup of all stale keys
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup(now)

            return True, 0

    def _cleanup(self, now: float) -> None:
        """Remove stale keys to prevent memory growth. Must be called under lock.

        Uses a 1-hour cutoff — any key with no timestamps newer than 1 hour
        is definitely expired (our longest window is 1 hour).
        """
        self._last_cleanup = now
        cutoff = now - 3600  # 1 hour max window
        stale_keys = []
        for k, timestamps in self._windows.items():
            # Prune old timestamps
            self._windows[k] = [t for t in timestamps if t > cutoff]
            if not self._windows[k]:
                stale_keys.append(k)
        for k in stale_keys:
            del self._windows[k]

    def reset(self) -> None:
        """Clear all tracked state. Useful for testing."""
        with self._lock:
            self._windows.clear()


# Global limiter instance shared across all rate limit dependencies
_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For from reverse proxy (Caddy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in the chain is the real client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(
    max_requests: int,
    window_seconds: int,
    key_func: Callable[[Request], str] | None = None,
):
    """Create a FastAPI dependency that enforces a rate limit.

    Args:
        max_requests: Maximum number of requests allowed in the window.
        window_seconds: Window size in seconds.
        key_func: Optional function to extract the rate limit key from the request.
                  Defaults to client IP. For per-user limits, pass a function
                  that extracts user_id from the request.

    Usage:
        login_limit = rate_limit(10, 60)  # 10 per minute by IP

        @app.post("/api/auth/login")
        async def login(data: dict, _=Depends(login_limit)):
            ...
    """
    async def dependency(request: Request) -> None:
        if key_func is not None:
            key = key_func(request)
        else:
            key = get_client_ip(request)

        # Include the path in the key so different endpoints have independent limits
        full_key = f"{request.url.path}:{key}"

        allowed, retry_after = _limiter.is_allowed(full_key, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


def reset_limiter() -> None:
    """Reset the global rate limiter. For testing only."""
    _limiter.reset()
