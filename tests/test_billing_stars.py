"""Tests for Telegram Stars billing flow in services/billing.py."""
from datetime import datetime, timedelta

import pytest

from db.models import (
    User, Subscription, SubscriptionPlan, SubscriptionStatus, SubscriptionProvider,
)
from db.session import init_db, SessionLocal
from services.billing import (
    PLANS_STARS,
    create_stars_invoice_payload,
    parse_stars_invoice_payload,
    confirm_stars_payment,
)


@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        # Clean tables between tests (in-memory DB but conftest only resets env var on import)
        for table in ("subscriptions", "users"):
            session.execute(__import__("sqlalchemy").text(f"DELETE FROM {table}"))
        session.commit()
        session.close()


@pytest.fixture
def user(db):
    u = User(tg_user_id=12345)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_payload_roundtrip(user):
    payload = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    assert payload.startswith("scrooge_stars:monthly:")
    parsed = parse_stars_invoice_payload(payload)
    assert parsed == {
        "plan": SubscriptionPlan.MONTHLY,
        "user_id": user.id,
        "nonce": payload.split(":")[-1],
    }


def test_payload_rejects_unknown_plan():
    assert parse_stars_invoice_payload("scrooge_stars:weekly:1:abc") is None


def test_payload_rejects_trial_plan(user):
    assert parse_stars_invoice_payload(f"scrooge_stars:trial:{user.id}:abc") is None


def test_payload_rejects_wrong_prefix():
    assert parse_stars_invoice_payload("other_prefix:monthly:1:abc") is None


def test_payload_rejects_empty():
    assert parse_stars_invoice_payload("") is None
    assert parse_stars_invoice_payload(None) is None


def test_payload_rejects_malformed_parts():
    assert parse_stars_invoice_payload("scrooge_stars:monthly:1") is None
    assert parse_stars_invoice_payload("scrooge_stars:monthly:notanint:abc") is None


def test_create_payload_rejects_trial():
    with pytest.raises(ValueError):
        create_stars_invoice_payload(1, SubscriptionPlan.TRIAL)


def test_confirm_stars_payment_creates_subscription(db, user):
    payload = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    sub = confirm_stars_payment(
        db,
        telegram_payment_charge_id="charge_abc",
        invoice_payload=payload,
        total_amount=PLANS_STARS[SubscriptionPlan.MONTHLY]["stars"],
    )
    assert sub is not None
    assert sub.user_id == user.id
    assert sub.provider == SubscriptionProvider.STARS
    assert sub.plan == SubscriptionPlan.MONTHLY
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.payment_id == "charge_abc"
    expected_expiry = sub.paid_at + timedelta(days=30)
    assert abs((sub.expires_at - expected_expiry).total_seconds()) < 5


def test_confirm_stars_payment_is_idempotent(db, user):
    payload = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    sub1 = confirm_stars_payment(db, "charge_dup", payload, 75)
    sub2 = confirm_stars_payment(db, "charge_dup", payload, 75)
    assert sub1.id == sub2.id
    assert db.query(Subscription).count() == 1


def test_confirm_stars_payment_extends_existing(db, user):
    """A second purchase while active extends from existing expiry, not from now."""
    payload1 = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    sub1 = confirm_stars_payment(db, "charge_1", payload1, 75)
    original_expiry = sub1.expires_at

    payload2 = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    sub2 = confirm_stars_payment(db, "charge_2", payload2, 75)

    assert sub2.id != sub1.id
    # Second subscription expires ~30 days after the first one expired
    assert sub2.expires_at >= original_expiry + timedelta(days=29)


def test_confirm_stars_payment_rejects_underpayment(db, user):
    payload = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    sub = confirm_stars_payment(db, "charge_low", payload, total_amount=1)
    assert sub is None
    assert db.query(Subscription).count() == 0


def test_confirm_stars_payment_rejects_bad_payload(db, user):
    sub = confirm_stars_payment(db, "charge_x", "garbage", 75)
    assert sub is None


def test_confirm_stars_payment_unknown_user(db):
    payload = create_stars_invoice_payload(99999, SubscriptionPlan.MONTHLY)
    sub = confirm_stars_payment(db, "charge_y", payload, 75)
    assert sub is None


def test_confirm_stars_payment_no_charge_id(db, user):
    payload = create_stars_invoice_payload(user.id, SubscriptionPlan.MONTHLY)
    sub = confirm_stars_payment(db, "", payload, 75)
    assert sub is None


def test_yearly_plan_pricing_and_duration(db, user):
    payload = create_stars_invoice_payload(user.id, SubscriptionPlan.YEARLY)
    sub = confirm_stars_payment(db, "charge_year", payload, 750)
    assert sub is not None
    assert sub.plan == SubscriptionPlan.YEARLY
    expected_expiry = sub.paid_at + timedelta(days=365)
    assert abs((sub.expires_at - expected_expiry).total_seconds()) < 5
