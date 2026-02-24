"""Tests for Stripe billing integration."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.web.jobs import _get_conn, _local, init_db
from src.web.users import (
    add_pages,
    create_user,
    get_user,
    init_users_db,
)
from src.web.billing import (
    CREDIT_PACKS,
    create_checkout_session,
    get_packs_for_display,
    get_user_transactions,
    handle_webhook,
    init_billing_db,
    record_transaction,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a temp database for each test."""
    import src.web.jobs as jobs_mod

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(jobs_mod, "DB_PATH", db_path)

    if hasattr(_local, "conn"):
        _local.conn = None

    init_db()
    init_users_db()
    init_billing_db()
    yield


@pytest.fixture
def user():
    """Create a test user."""
    return create_user(email="buyer@example.com", password_hash="hash", display_name="Buyer")


class TestCreditPacks:
    def test_packs_defined(self):
        assert "starter" in CREDIT_PACKS
        assert "standard" in CREDIT_PACKS
        assert "bulk" in CREDIT_PACKS

    def test_starter_pack_values(self):
        pack = CREDIT_PACKS["starter"]
        assert pack["pages"] == 50
        assert pack["price_cents"] == 500

    def test_standard_pack_values(self):
        pack = CREDIT_PACKS["standard"]
        assert pack["pages"] == 200
        assert pack["price_cents"] == 1500

    def test_bulk_pack_values(self):
        pack = CREDIT_PACKS["bulk"]
        assert pack["pages"] == 500
        assert pack["price_cents"] == 3000

    def test_get_packs_for_display(self):
        packs = get_packs_for_display()
        assert len(packs) == 3
        # Should not include stripe_price_id
        for pack in packs:
            assert "stripe_price_id" not in pack
            assert "id" in pack
            assert "name" in pack
            assert "pages" in pack
            assert "price_cents" in pack
            assert "price_display" in pack
            assert "per_page" in pack

    def test_packs_display_order(self):
        packs = get_packs_for_display()
        ids = [p["id"] for p in packs]
        assert ids == ["starter", "standard", "bulk"]


class TestInitBillingDb:
    def test_creates_transactions_table(self):
        conn = _get_conn()
        cursor = conn.execute("PRAGMA table_info(transactions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "id" in columns
        assert "user_id" in columns
        assert "pack_id" in columns
        assert "pages" in columns
        assert "amount_cents" in columns
        assert "stripe_session_id" in columns
        assert "stripe_payment_intent" in columns
        assert "status" in columns
        assert "created_at" in columns

    def test_idempotent(self):
        # Should not raise on second call
        init_billing_db()
        init_billing_db()


class TestRecordTransaction:
    def test_basic_record(self, user):
        txn_id = record_transaction(
            user_id=user.id,
            pack_id="starter",
            pages=50,
            amount_cents=500,
            stripe_session_id="cs_test_abc123",
            stripe_payment_intent="pi_test_xyz",
        )
        assert len(txn_id) == 16

        # Verify in DB
        conn = _get_conn()
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        assert row is not None
        data = dict(row)
        assert data["user_id"] == user.id
        assert data["pack_id"] == "starter"
        assert data["pages"] == 50
        assert data["amount_cents"] == 500
        assert data["status"] == "completed"

    def test_duplicate_stripe_session_fails(self, user):
        record_transaction(
            user_id=user.id,
            pack_id="starter",
            pages=50,
            amount_cents=500,
            stripe_session_id="cs_test_dup",
        )
        with pytest.raises(sqlite3.IntegrityError):
            record_transaction(
                user_id=user.id,
                pack_id="starter",
                pages=50,
                amount_cents=500,
                stripe_session_id="cs_test_dup",
            )


class TestGetUserTransactions:
    def test_empty(self, user):
        txns = get_user_transactions(user.id)
        assert txns == []

    def test_returns_transactions(self, user):
        record_transaction(user.id, "starter", 50, 500, "cs_1")
        record_transaction(user.id, "standard", 200, 1500, "cs_2")

        txns = get_user_transactions(user.id)
        assert len(txns) == 2
        # Newest first
        assert txns[0]["pack_id"] == "standard"
        assert txns[1]["pack_id"] == "starter"

    def test_user_isolation(self, user):
        user2 = create_user(email="other@example.com")
        record_transaction(user.id, "starter", 50, 500, "cs_a")
        record_transaction(user2.id, "bulk", 500, 3000, "cs_b")

        txns = get_user_transactions(user.id)
        assert len(txns) == 1
        assert txns[0]["pack_id"] == "starter"


class TestCreateCheckoutSession:
    def test_invalid_pack_raises(self, user, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        with pytest.raises(ValueError, match="Unknown pack"):
            create_checkout_session(user.id, "nonexistent", "http://ok", "http://cancel")

    def test_no_stripe_key_raises(self, user, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
            create_checkout_session(user.id, "starter", "http://ok", "http://cancel")

    @patch("src.web.billing.stripe.checkout.Session.create")
    def test_creates_session_with_price_data(self, mock_create, user, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/pay/cs_test_123"
        mock_create.return_value = mock_session

        url = create_checkout_session(user.id, "starter", "http://ok", "http://cancel")

        assert url == "https://checkout.stripe.com/pay/cs_test_123"
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["mode"] == "payment"
        assert call_kwargs["success_url"] == "http://ok"
        assert call_kwargs["cancel_url"] == "http://cancel"
        assert call_kwargs["metadata"]["user_id"] == user.id
        assert call_kwargs["metadata"]["pack_id"] == "starter"
        assert call_kwargs["metadata"]["pages"] == "50"
        # Should use price_data since no stripe_price_id set
        line_item = call_kwargs["line_items"][0]
        assert "price_data" in line_item
        assert line_item["price_data"]["unit_amount"] == 500

    @patch("src.web.billing.stripe.checkout.Session.create")
    def test_uses_stripe_price_id_when_set(self, mock_create, user, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        # Temporarily set stripe_price_id
        original = CREDIT_PACKS["starter"]["stripe_price_id"]
        CREDIT_PACKS["starter"]["stripe_price_id"] = "price_test_abc"
        try:
            mock_session = MagicMock()
            mock_session.url = "https://checkout.stripe.com/pay/cs_test_456"
            mock_create.return_value = mock_session

            create_checkout_session(user.id, "starter", "http://ok", "http://cancel")

            call_kwargs = mock_create.call_args[1]
            line_item = call_kwargs["line_items"][0]
            assert line_item["price"] == "price_test_abc"
            assert "price_data" not in line_item
        finally:
            CREDIT_PACKS["starter"]["stripe_price_id"] = original


class TestHandleWebhook:
    def _build_event(self, user_id, pack_id="starter", pages=50,
                     session_id="cs_test_wh1", amount=500, payment_intent="pi_test_1"):
        """Build a mock Stripe checkout.session.completed event."""
        return {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session_id,
                    "payment_intent": payment_intent,
                    "amount_total": amount,
                    "metadata": {
                        "user_id": user_id,
                        "pack_id": pack_id,
                        "pages": str(pages),
                    },
                }
            }
        }

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_credits_pages_on_checkout_completed(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = self._build_event(user.id)
        mock_construct.return_value = event

        initial_balance = user.pages_balance  # 20

        result = handle_webhook(b"payload", "sig_header")

        assert result["status"] == "credited"
        assert result["pages"] == 50

        updated_user = get_user(user.id)
        assert updated_user.pages_balance == initial_balance + 50

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_flips_tier_to_paid(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        assert user.tier == "free"

        event = self._build_event(user.id)
        mock_construct.return_value = event

        handle_webhook(b"payload", "sig")

        updated_user = get_user(user.id)
        assert updated_user.tier == "paid"

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_records_transaction(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = self._build_event(user.id, session_id="cs_rec_test")
        mock_construct.return_value = event

        result = handle_webhook(b"payload", "sig")

        txns = get_user_transactions(user.id)
        assert len(txns) == 1
        assert txns[0]["stripe_session_id"] == "cs_rec_test"
        assert txns[0]["pack_id"] == "starter"
        assert txns[0]["pages"] == 50

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_idempotent_duplicate_webhook(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = self._build_event(user.id, session_id="cs_idem_test")
        mock_construct.return_value = event

        result1 = handle_webhook(b"payload", "sig")
        assert result1["status"] == "credited"

        # Second delivery of same event
        result2 = handle_webhook(b"payload", "sig")
        assert result2["status"] == "already_processed"

        # Pages only credited once
        updated = get_user(user.id)
        assert updated.pages_balance == 20 + 50  # not 20 + 100

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_ignores_non_checkout_events(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        mock_construct.return_value = {
            "type": "payment_intent.succeeded",
            "data": {"object": {}},
        }

        result = handle_webhook(b"payload", "sig")
        assert result["status"] == "ignored"

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_missing_metadata(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        mock_construct.return_value = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_no_meta",
                    "metadata": {},
                }
            }
        }

        result = handle_webhook(b"payload", "sig")
        assert result["status"] == "error"
        assert "missing metadata" in result["reason"]

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_user_not_found(self, mock_construct, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_no_user",
                    "metadata": {
                        "user_id": "nonexistent",
                        "pack_id": "starter",
                        "pages": "50",
                    },
                }
            }
        }
        mock_construct.return_value = event

        result = handle_webhook(b"payload", "sig")
        assert result["status"] == "error"
        assert "user not found" in result["reason"]

    def test_no_webhook_secret_raises(self, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        with pytest.raises(ValueError, match="STRIPE_WEBHOOK_SECRET"):
            handle_webhook(b"payload", "sig")

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_invalid_signature_raises(self, mock_construct, monkeypatch):
        import stripe as stripe_mod
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        mock_construct.side_effect = stripe_mod.SignatureVerificationError("bad sig", "sig_header")

        with pytest.raises(ValueError, match="Invalid webhook signature"):
            handle_webhook(b"payload", "bad_sig")

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_bulk_pack_credits_500_pages(self, mock_construct, user, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        event = self._build_event(user.id, pack_id="bulk", pages=500,
                                  session_id="cs_bulk", amount=3000)
        mock_construct.return_value = event

        result = handle_webhook(b"payload", "sig")
        assert result["pages"] == 500

        updated = get_user(user.id)
        assert updated.pages_balance == 20 + 500

    @patch("src.web.billing.stripe.Webhook.construct_event")
    def test_stacking_purchases(self, mock_construct, user, monkeypatch):
        """Multiple purchases stack on existing balance."""
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

        # First purchase
        event1 = self._build_event(user.id, session_id="cs_stack_1")
        mock_construct.return_value = event1
        handle_webhook(b"payload", "sig")

        # Second purchase
        event2 = self._build_event(user.id, pack_id="standard", pages=200,
                                   session_id="cs_stack_2", amount=1500)
        mock_construct.return_value = event2
        handle_webhook(b"payload", "sig")

        updated = get_user(user.id)
        assert updated.pages_balance == 20 + 50 + 200  # 270
