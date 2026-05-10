"""Tests for bot/middleware.py paywall + subscription checks."""
from datetime import datetime, timedelta

import pytest

from bot.middleware import (
    _has_premium_access,
    _get_active_subscription,
    _get_trial_days_left,
    check_subscription,
    is_pro,
)
from db.models import User, Subscription, SubscriptionPlan, SubscriptionStatus, SubscriptionProvider
from db.session import init_db, SessionLocal


@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for table in ("subscriptions", "users"):
            session.execute(__import__("sqlalchemy").text(f"DELETE FROM {table}"))
        session.commit()
        session.close()


@pytest.fixture
def user(db):
    u = User(tg_user_id=42)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_sub(db, user, days, plan=SubscriptionPlan.MONTHLY, status=SubscriptionStatus.ACTIVE):
    now = datetime.utcnow()
    sub = Subscription(
        user_id=user.id,
        plan=plan,
        status=status,
        provider=SubscriptionProvider.STARS,
        payment_id="t",
        paid_at=now,
        expires_at=now + timedelta(days=days),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def test_has_premium_false_for_new_user(db, user):
    assert _has_premium_access(db, user) is False


def test_has_premium_true_with_active_sub(db, user):
    _make_sub(db, user, days=30)
    assert _has_premium_access(db, user) is True


def test_has_premium_false_with_expired_sub(db, user):
    _make_sub(db, user, days=-1)
    assert _has_premium_access(db, user) is False


def test_has_premium_false_with_cancelled_sub(db, user):
    _make_sub(db, user, days=30, status=SubscriptionStatus.CANCELLED)
    assert _has_premium_access(db, user) is False


def test_get_active_subscription_returns_latest(db, user):
    _make_sub(db, user, days=10)
    later = _make_sub(db, user, days=100)
    active = _get_active_subscription(db, user)
    assert active.id == later.id


def test_trial_days_left_none_when_not_started(user):
    assert _get_trial_days_left(user) is None


def test_trial_days_left_positive_after_activation(db, user):
    user.trial_activated_at = datetime.utcnow() - timedelta(days=3)
    db.commit()
    assert _get_trial_days_left(user) == 10  # 14 - 3


def test_trial_days_left_zero_after_expiry(db, user):
    user.trial_activated_at = datetime.utcnow() - timedelta(days=20)
    db.commit()
    assert _get_trial_days_left(user) == 0


def test_is_pro_unknown_user(db):
    assert is_pro(db, 99999) is False


def test_is_pro_for_known_user(db, user):
    _make_sub(db, user, days=30)
    assert is_pro(db, user.tg_user_id) is True


@pytest.mark.asyncio
async def test_check_subscription_blocks_no_sub(db, user):
    allowed, text, keyboard = await check_subscription(db, user)
    assert allowed is False
    assert "Premium" in text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_check_subscription_offers_trial_for_new_user(db, user):
    allowed, text, _ = await check_subscription(db, user)
    assert allowed is False
    assert "Попробуйте бесплатно" in text


@pytest.mark.asyncio
async def test_check_subscription_no_trial_button_after_used(db, user):
    user.trial_used = True
    db.commit()
    allowed, text, _ = await check_subscription(db, user)
    assert allowed is False
    assert "Попробуйте бесплатно" not in text


@pytest.mark.asyncio
async def test_check_subscription_allows_with_active_sub(db, user):
    _make_sub(db, user, days=30)
    allowed, text, keyboard = await check_subscription(db, user)
    assert allowed is True
    assert text is None
    assert keyboard is None
