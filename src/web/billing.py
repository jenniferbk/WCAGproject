"""Stripe billing integration for credit pack purchases.

Handles Stripe Checkout session creation, webhook processing,
and transaction recording. Pages are credited via webhook (reliable,
server-side), not via the redirect.
"""

from __future__ import annotations

import logging
import os
from src.web.db import begin_immediate, is_integrity_error
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import stripe

from src.web.jobs import _get_conn
from src.web.users import get_user, update_user

logger = logging.getLogger(__name__)

# ── Credit pack definitions ──────────────────────────────────────

CREDIT_PACKS = {
    "starter": {
        "id": "starter",
        "name": "Starter",
        "pages": 50,
        "price_cents": 500,
        "price_display": "$5",
        "per_page": "$0.10",
        "description": "50 pages",
        "stripe_price_id": os.environ.get("STRIPE_PRICE_STARTER", ""),
    },
    "standard": {
        "id": "standard",
        "name": "Standard",
        "pages": 200,
        "price_cents": 1500,
        "price_display": "$15",
        "per_page": "$0.075",
        "description": "200 pages",
        "stripe_price_id": os.environ.get("STRIPE_PRICE_STANDARD", ""),
    },
    "bulk": {
        "id": "bulk",
        "name": "Bulk",
        "pages": 500,
        "price_cents": 3000,
        "price_display": "$30",
        "per_page": "$0.06",
        "description": "500 pages",
        "stripe_price_id": os.environ.get("STRIPE_PRICE_BULK", ""),
    },
}


def get_packs_for_display() -> list[dict]:
    """Return packs list suitable for frontend display (no stripe_price_id)."""
    packs = []
    for pack in CREDIT_PACKS.values():
        packs.append({
            "id": pack["id"],
            "name": pack["name"],
            "pages": pack["pages"],
            "price_cents": pack["price_cents"],
            "price_display": pack["price_display"],
            "per_page": pack["per_page"],
            "description": pack["description"],
        })
    return packs


# ── Database ─────────────────────────────────────────────────────

def init_billing_db() -> None:
    """Create the transactions table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            pack_id TEXT NOT NULL,
            pages INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            stripe_session_id TEXT UNIQUE,
            stripe_payment_intent TEXT,
            status TEXT DEFAULT 'completed',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def record_transaction(
    user_id: str,
    pack_id: str,
    pages: int,
    amount_cents: int,
    stripe_session_id: str,
    stripe_payment_intent: str = "",
) -> str:
    """Record a completed transaction. Returns the transaction ID."""
    conn = _get_conn()
    txn_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO transactions
           (id, user_id, pack_id, pages, amount_cents,
            stripe_session_id, stripe_payment_intent, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
        (txn_id, user_id, pack_id, pages, amount_cents,
         stripe_session_id, stripe_payment_intent, now),
    )
    conn.commit()
    return txn_id


def get_user_transactions(user_id: str) -> list[dict]:
    """Get transaction history for a user, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ── Stripe Checkout ──────────────────────────────────────────────

def _configure_stripe() -> None:
    """Set Stripe API key from environment."""
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise ValueError("STRIPE_SECRET_KEY environment variable is not set")
    stripe.api_key = key


def create_checkout_session(user_id: str, pack_id: str, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout Session and return the checkout URL.

    Args:
        user_id: The user purchasing credits.
        pack_id: One of the CREDIT_PACKS keys.
        success_url: URL to redirect to after successful payment.
        cancel_url: URL to redirect to if user cancels.

    Returns:
        The Stripe Checkout Session URL.

    Raises:
        ValueError: If pack_id is invalid or Stripe is not configured.
    """
    if pack_id not in CREDIT_PACKS:
        raise ValueError(f"Unknown pack: {pack_id}")

    pack = CREDIT_PACKS[pack_id]
    _configure_stripe()

    session_params = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {
            "user_id": user_id,
            "pack_id": pack_id,
        },
        "client_reference_id": user_id,
    }

    # Use Stripe Price ID if configured, otherwise use line_items with price_data
    if pack["stripe_price_id"]:
        session_params["line_items"] = [{"price": pack["stripe_price_id"], "quantity": 1}]
    else:
        session_params["line_items"] = [{
            "price_data": {
                "currency": "usd",
                "unit_amount": pack["price_cents"],
                "product_data": {
                    "name": f"A11y Remediation — {pack['name']} Pack",
                    "description": f"{pack['pages']} page credits for document accessibility remediation",
                },
            },
            "quantity": 1,
        }]

    session = stripe.checkout.Session.create(**session_params)
    return session.url


# ── Webhook ──────────────────────────────────────────────────────

class WebhookError(Exception):
    """Raised for webhook errors that should trigger Stripe retry (5xx)."""
    pass


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and process a Stripe webhook event.

    Args:
        payload: Raw request body bytes.
        sig_header: Stripe-Signature header value.

    Returns:
        Dict with processing result.

    Raises:
        ValueError: If signature verification fails (4xx — don't retry).
        WebhookError: If processing fails transiently (5xx — Stripe should retry).
    """
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.SignatureVerificationError:
        raise ValueError("Invalid webhook signature")

    if event["type"] != "checkout.session.completed":
        return {"status": "ignored", "event_type": event["type"]}

    session = event["data"]["object"]
    metadata = session.get("metadata", {})
    user_id = metadata.get("user_id", "")
    pack_id = metadata.get("pack_id", "")
    stripe_session_id = session.get("id", "")
    stripe_payment_intent = session.get("payment_intent", "") or ""
    amount_cents = session.get("amount_total", 0)

    # Validate pack_id against server-side definitions (don't trust metadata for pages)
    if not user_id or pack_id not in CREDIT_PACKS:
        logger.warning("Webhook bad metadata: user_id=%s pack_id=%s", user_id, pack_id)
        return {"status": "error", "reason": "invalid metadata"}

    pages = CREDIT_PACKS[pack_id]["pages"]

    # Check user exists — transient failure should trigger retry
    user = get_user(user_id)
    if not user:
        logger.error("Webhook: user %s not found", user_id)
        raise WebhookError(f"User {user_id} not found")

    # Atomic idempotency: insert transaction first within an exclusive lock.
    # If duplicate, the UNIQUE constraint on stripe_session_id prevents double-credit.
    conn = _get_conn()
    txn_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()

    try:
        begin_immediate(conn)
        conn.execute(
            """INSERT INTO transactions
               (id, user_id, pack_id, pages, amount_cents,
                stripe_session_id, stripe_payment_intent, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
            (txn_id, user_id, pack_id, pages, amount_cents,
             stripe_session_id, stripe_payment_intent, now),
        )
        # Credit pages within the same transaction
        conn.execute(
            "UPDATE users SET pages_balance = pages_balance + ?, updated_at = ? WHERE id = ?",
            (pages, now, user_id),
        )
        conn.commit()
    except Exception as e:
        if is_integrity_error(e):
            conn.rollback()
            logger.info("Webhook: session %s already processed", stripe_session_id)
            return {"status": "already_processed"}
        raise

    # Flip tier to 'paid' on first purchase (outside critical section, idempotent)
    if user.tier == "free":
        update_user(user_id, tier="paid")

    logger.info("Credited %d pages to user %s (pack=%s, txn=%s)",
                pages, user_id, pack_id, txn_id)

    return {"status": "credited", "transaction_id": txn_id, "pages": pages}
