"""FastAPI authentication dependencies."""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException

from src.web.auth import COOKIE_NAME, verify_token
from src.web.users import User, get_user


async def get_current_user(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> User | None:
    """Extract user from session cookie. Returns None if not authenticated."""
    if not session:
        return None
    payload = verify_token(session)
    if not payload:
        return None
    return get_user(payload["sub"])


async def require_user(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> User:
    """Require an authenticated user. Raises 401 if not logged in."""
    user = await get_current_user(session)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    """Require an admin user. Raises 403 if not admin."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
