"""Tests for billing service: trial activation, subscription management."""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from db.models import (
    User, Subscription, SubscriptionPlan, SubscriptionStatus,
    Budget, Base,
)
from db.session import SessionLocal, init_db, engine
from services.ledger import get_or_create_user, create_account
from services.billing import activate_trial, get_subscription_info, expire_subscriptions
from bot.middleware import _has_premium_access, _get_trial_days_left, get_usage_stats


@pytest.fixture(scope="function")
def db():
    """Create test database session."""
    init_db()
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def user(db: Session):
    """Create test user."""
    return get_or_create_user(db, 99999, "Europe/Moscow")


# === Trial Tests ===

def test_activate_trial(db: Session, user: User):
    """Test activating trial subscription."""
    sub = activate_trial(db, user.id)
    assert sub is not None
    assert sub.plan == SubscriptionPlan.TRIAL
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.expires_at > datetime.utcnow()

    db.refresh(user)
    assert user.trial_used is True
    assert user.trial_activated_at is not None


def test_activate_trial_only_once(db: Session, user: User):
    """Test trial can only be activated once."""
    sub1 = activate_trial(db, user.id)
    assert sub1 is not None

    sub2 = activate_trial(db, user.id)
    assert sub2 is None


def test_trial_gives_premium(db: Session, user: User):
    """Test trial grants premium access."""
    assert _has_premium_access(db, user) is False
    activate_trial(db, user.id)
    assert _has_premium_access(db, user) is True


def test_trial_days_left(db: Session, user: User):
    """Test trial days calculation."""
    assert _get_trial_days_left(user) is None

    activate_trial(db, user.id)
    db.refresh(user)

    days = _get_trial_days_left(user)
    assert days is not None
    assert 13 <= days <= 14


def test_expired_trial_no_premium(db: Session, user: User):
    """Test expired trial does not give premium."""
    sub = activate_trial(db, user.id)
    # Manually expire it
    sub.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()

    assert _has_premium_access(db, user) is False


# === Admin bypass tests ===

def test_admin_bypasses_paywall(db: Session, user: User, monkeypatch):
    """Telegram IDs in ADMIN_USER_IDS get premium without a subscription."""
    monkeypatch.setenv("ADMIN_USER_IDS", f"{user.tg_user_id},111")
    assert _has_premium_access(db, user) is True


def test_admin_bypass_ignores_expired_sub(db: Session, user: User, monkeypatch):
    """Admin access works even if their subscription has expired."""
    sub = activate_trial(db, user.id)
    sub.expires_at = datetime.utcnow() - timedelta(days=30)
    db.commit()
    monkeypatch.setenv("ADMIN_USER_IDS", str(user.tg_user_id))
    assert _has_premium_access(db, user) is True


def test_non_admin_still_blocked(db: Session, user: User, monkeypatch):
    """Users not in ADMIN_USER_IDS still hit the paywall."""
    monkeypatch.setenv("ADMIN_USER_IDS", "111,222")
    assert _has_premium_access(db, user) is False


def test_empty_admin_env_is_safe(db: Session, user: User, monkeypatch):
    """Empty / unset ADMIN_USER_IDS doesn't crash and grants no premium."""
    monkeypatch.setenv("ADMIN_USER_IDS", "")
    assert _has_premium_access(db, user) is False
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    assert _has_premium_access(db, user) is False


def test_malformed_admin_ids_ignored(db: Session, user: User, monkeypatch):
    """Non-numeric tokens in ADMIN_USER_IDS are ignored, not crash."""
    monkeypatch.setenv("ADMIN_USER_IDS", f"  ,abc, {user.tg_user_id} , ")
    assert _has_premium_access(db, user) is True


# === Subscription Info Tests ===

def test_subscription_info_no_sub(db: Session, user: User):
    """Test subscription info for user without subscription."""
    info = get_subscription_info(db, user)
    assert info["active"] is False
    assert info["trial_available"] is True


def test_subscription_info_with_trial(db: Session, user: User):
    """Test subscription info for user with trial."""
    activate_trial(db, user.id)
    info = get_subscription_info(db, user)
    assert info["active"] is True
    assert info["plan"] == "trial"
    assert info["days_left"] >= 13


def test_subscription_info_trial_used(db: Session, user: User):
    """Test trial_available after trial used and expired."""
    sub = activate_trial(db, user.id)
    sub.expires_at = datetime.utcnow() - timedelta(days=1)
    sub.status = SubscriptionStatus.EXPIRED
    db.commit()

    info = get_subscription_info(db, user)
    assert info["active"] is False
    assert info["trial_available"] is False


# === Subscription Expiry Tests ===

def test_expire_subscriptions(db: Session, user: User):
    """Test automatic subscription expiry."""
    sub = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime.utcnow() - timedelta(days=1),
    )
    db.add(sub)
    db.commit()

    count = expire_subscriptions(db)
    assert count == 1

    db.refresh(sub)
    assert sub.status == SubscriptionStatus.EXPIRED


def test_expire_does_not_touch_active(db: Session, user: User):
    """Test expiry doesn't affect active subscriptions."""
    sub = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(sub)
    db.commit()

    count = expire_subscriptions(db)
    assert count == 0

    db.refresh(sub)
    assert sub.status == SubscriptionStatus.ACTIVE


# === Budget Tests ===

def test_create_budget(db: Session, user: User):
    """Test creating a budget."""
    budget = Budget(
        user_id=user.id,
        category="Кафе и кофе",
        monthly_limit=Decimal("3000.00"),
        currency="RUB",
    )
    db.add(budget)
    db.commit()
    db.refresh(budget)

    assert budget.category == "Кафе и кофе"
    assert budget.monthly_limit == Decimal("3000.00")


# === Usage Stats Tests ===

def test_get_usage_stats_no_user(db: Session):
    """Test usage stats for nonexistent user."""
    stats = get_usage_stats(0)
    assert stats["is_premium"] is False


def test_get_usage_stats_premium(db: Session, user: User):
    """Test usage stats for premium user."""
    activate_trial(db, user.id)
    stats = get_usage_stats(user.tg_user_id)
    assert stats["is_premium"] is True


# === Edge Case Tests ===

def test_no_premium_without_subscription(db: Session, user: User):
    """Test user without any subscription has no premium."""
    assert _has_premium_access(db, user) is False


def test_subscription_extends_from_existing(db: Session, user: User):
    """Test that adding a subscription while active extends from expiry date."""
    # Create an active subscription
    sub1 = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime.utcnow() + timedelta(days=15),
    )
    db.add(sub1)
    db.commit()

    assert _has_premium_access(db, user) is True

    # Second subscription should still be valid
    sub2 = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.YEARLY,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime.utcnow() + timedelta(days=380),
    )
    db.add(sub2)
    db.commit()

    info = get_subscription_info(db, user)
    assert info["active"] is True
    assert info["days_left"] >= 379


def test_trial_days_left_zero_when_expired(db: Session, user: User):
    """Test trial_days_left returns 0 for expired trial."""
    sub = activate_trial(db, user.id)
    sub.expires_at = datetime.utcnow() - timedelta(hours=1)
    user.trial_activated_at = datetime.utcnow() - timedelta(days=15)
    db.commit()
    db.refresh(user)

    days = _get_trial_days_left(user)
    assert days == 0


def test_expire_only_active_past_due(db: Session, user: User):
    """Test expire_subscriptions only touches ACTIVE + past-due subs."""
    # Already expired sub — should not be touched
    sub_expired = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.EXPIRED,
        expires_at=datetime.utcnow() - timedelta(days=10),
    )
    # Cancelled sub — should not be touched
    sub_cancelled = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.MONTHLY,
        status=SubscriptionStatus.CANCELLED,
        expires_at=datetime.utcnow() - timedelta(days=5),
    )
    db.add_all([sub_expired, sub_cancelled])
    db.commit()

    count = expire_subscriptions(db)
    assert count == 0

    db.refresh(sub_expired)
    db.refresh(sub_cancelled)
    assert sub_expired.status == SubscriptionStatus.EXPIRED
    assert sub_cancelled.status == SubscriptionStatus.CANCELLED
