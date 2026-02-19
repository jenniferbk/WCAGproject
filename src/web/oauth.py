"""OAuth2 configuration for Google and Microsoft providers.

Uses authlib for OAuth2 flows. Providers are only initialized
if their client ID/secret env vars are set.
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

# Lazy-initialized OAuth clients
_google_client = None
_microsoft_client = None


class OAuthProvider:
    """Wrapper around an OAuth2 provider config."""

    def __init__(self, name: str, client_id: str, client_secret: str,
                 authorize_url: str, token_url: str, userinfo_url: str, scopes: str):
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.userinfo_url = userinfo_url
        self.scopes = scopes

    async def create_authorization_url(self, redirect_uri: str) -> str:
        """Build the OAuth authorization URL."""
        from authlib.integrations.httpx_client import AsyncOAuth2Client

        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
            scope=self.scopes,
        )
        url, _state = client.create_authorization_url(self.authorize_url)
        return url

    async def fetch_user_info(self, code: str, redirect_uri: str) -> dict:
        """Exchange code for token, then fetch user info."""
        from authlib.integrations.httpx_client import AsyncOAuth2Client

        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri,
        )
        token = await client.fetch_token(self.token_url, code=code)
        resp = await client.get(self.userinfo_url)
        return resp.json()


def _get_google_oauth() -> OAuthProvider | None:
    global _google_client
    if _google_client:
        return _google_client

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    _google_client = OAuthProvider(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes="openid email profile",
    )
    return _google_client


def _get_microsoft_oauth() -> OAuthProvider | None:
    global _microsoft_client
    if _microsoft_client:
        return _microsoft_client

    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "")
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    _microsoft_client = OAuthProvider(
        name="microsoft",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        scopes="openid email profile User.Read",
    )
    return _microsoft_client


# Public accessors used by app.py
@property
def google_oauth():
    provider = _get_google_oauth()
    if not provider:
        raise RuntimeError("Google OAuth not configured")
    return provider


@property
def microsoft_oauth():
    provider = _get_microsoft_oauth()
    if not provider:
        raise RuntimeError("Microsoft OAuth not configured")
    return provider


async def handle_google_callback(code: str, state: str):
    """Handle Google OAuth callback, create/find user."""
    from src.web.users import create_user, get_user_by_email, get_user_by_oauth

    provider = _get_google_oauth()
    if not provider:
        raise RuntimeError("Google OAuth not configured")

    info = await provider.fetch_user_info(code, "/api/auth/google/callback")
    email = info.get("email", "")
    provider_id = info.get("sub", "")
    name = info.get("name", email.split("@")[0])

    # Check if user exists by OAuth ID or email
    user = get_user_by_oauth("google", provider_id)
    if not user:
        user = get_user_by_email(email)
    if not user:
        user = create_user(
            email=email,
            display_name=name,
            auth_provider="google",
            oauth_provider_id=provider_id,
        )
    return user


async def handle_microsoft_callback(code: str, state: str):
    """Handle Microsoft OAuth callback, create/find user."""
    from src.web.users import create_user, get_user_by_email, get_user_by_oauth

    provider = _get_microsoft_oauth()
    if not provider:
        raise RuntimeError("Microsoft OAuth not configured")

    info = await provider.fetch_user_info(code, "/api/auth/microsoft/callback")
    email = info.get("mail") or info.get("userPrincipalName", "")
    provider_id = info.get("id", "")
    name = info.get("displayName", email.split("@")[0])

    user = get_user_by_oauth("microsoft", provider_id)
    if not user:
        user = get_user_by_email(email)
    if not user:
        user = create_user(
            email=email,
            display_name=name,
            auth_provider="microsoft",
            oauth_provider_id=provider_id,
        )
    return user
