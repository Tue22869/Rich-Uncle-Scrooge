"""Billing service: YooKassa payments, trial activation, subscription management."""
import os
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict

from sqlalchemy.orm import Session

from db.models import User, Subscription, SubscriptionPlan, SubscriptionStatus

logger = logging.getLogger(__name__)

# Plan prices and durations
PLANS = {
    SubscriptionPlan.MONTHLY: {"price": "190.00", "currency": "RUB", "days": 30, "label": "Месяц"},
    SubscriptionPlan.YEARLY: {"price": "1900.00", "currency": "RUB", "days": 365, "label": "Год"},
}


def _get_yookassa_configured() -> bool:
    """Check if YooKassa credentials are configured."""
    return bool(os.getenv("YOOKASSA_SHOP_ID") and os.getenv("YOOKASSA_SECRET_KEY"))


def _configure_yookassa():
    """Configure YooKassa SDK with credentials from env."""
    from yookassa import Configuration
    Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
    Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")


def activate_trial(db: Session, user_id: int) -> Optional[Subscription]:
    """Activate 14-day free trial for a user. Returns None if trial already used."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    if user.trial_used:
        return None

    now = datetime.utcnow()
    subscription = Subscription(
        user_id=user.id,
        plan=SubscriptionPlan.TRIAL,
        status=SubscriptionStatus.ACTIVE,
        paid_at=now,
        expires_at=now + timedelta(days=14),
    )
    db.add(subscription)

    user.trial_used = True
    user.trial_activated_at = now
    db.commit()
    db.refresh(subscription)

    logger.info(f"Trial activated for user {user_id}, expires {subscription.expires_at}")
    return subscription


def create_payment(db: Session, user_id: int, plan: SubscriptionPlan, return_url: str = "") -> Optional[Dict]:
    """
    Create a YooKassa payment for the given plan.
    Returns payment dict with 'confirmation_url' and 'payment_id', or None on error.
    """
    if not _get_yookassa_configured():
        logger.error("YooKassa not configured")
        return None

    plan_info = PLANS.get(plan)
    if not plan_info:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    try:
        _configure_yookassa()
        from yookassa import Payment

        idempotency_key = str(uuid.uuid4())
        payment = Payment.create(
            {
                "amount": {
                    "value": plan_info["price"],
                    "currency": plan_info["currency"],
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url or "https://t.me/uncle_scrooge_bot",
                },
                "capture": True,
                "description": f"Подписка «Дядя Скрудж» — {plan_info['label']}",
                "metadata": {
                    "user_id": user.id,
                    "tg_user_id": user.tg_user_id,
                    "plan": plan.value,
                },
            },
            idempotency_key,
        )

        confirmation_url = payment.confirmation.confirmation_url if payment.confirmation else None
        logger.info(f"Payment created: {payment.id} for user {user_id}, plan={plan.value}")

        return {
            "payment_id": payment.id,
            "confirmation_url": confirmation_url,
            "status": payment.status,
        }

    except Exception as e:
        logger.error(f"Failed to create payment: {e}", exc_info=True)
        return None


def check_payment_status(payment_id: str) -> Optional[str]:
    """Check YooKassa payment status. Returns 'succeeded', 'pending', 'canceled', etc."""
    if not _get_yookassa_configured():
        return None

    try:
        _configure_yookassa()
        from yookassa import Payment
        payment = Payment.find_one(payment_id)
        return payment.status
    except Exception as e:
        logger.error(f"Failed to check payment {payment_id}: {e}")
        return None


def confirm_payment(db: Session, payment_id: str) -> Optional[Subscription]:
    """
    Confirm a successful payment and create/extend subscription.
    Called after YooKassa webhook or manual status check.
    """
    if not _get_yookassa_configured():
        return None

    try:
        _configure_yookassa()
        from yookassa import Payment
        payment = Payment.find_one(payment_id)

        if payment.status != "succeeded":
            return None

        metadata = payment.metadata or {}
        user_id = metadata.get("user_id")
        plan_str = metadata.get("plan")

        if not user_id or not plan_str:
            logger.error(f"Payment {payment_id} missing metadata")
            return None

        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            return None

        # Check if already processed
        existing = db.query(Subscription).filter(Subscription.payment_id == payment_id).first()
        if existing:
            return existing

        plan = SubscriptionPlan(plan_str)
        plan_info = PLANS[plan]

        now = datetime.utcnow()

        # Extend from current expiry if user already has active sub
        current_sub = (
            db.query(Subscription)
            .filter(
                Subscription.user_id == user.id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .first()
        )
        start_from = current_sub.expires_at if current_sub else now

        subscription = Subscription(
            user_id=user.id,
            plan=plan,
            status=SubscriptionStatus.ACTIVE,
            payment_id=payment_id,
            paid_at=now,
            expires_at=start_from + timedelta(days=plan_info["days"]),
        )
        db.add(subscription)
        db.commit()
        db.refresh(subscription)

        logger.info(f"Subscription confirmed: {subscription.id} for user {user_id}")
        return subscription

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to confirm payment {payment_id}: {e}", exc_info=True)
        return None


def get_subscription_info(db: Session, user: User) -> Dict:
    """Get human-readable subscription info for display."""

    now = datetime.utcnow()
    active_sub = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
        .first()
    )

    if not active_sub:
        return {
            "active": False,
            "plan": None,
            "expires_at": None,
            "trial_available": not user.trial_used,
        }

    days_left = (active_sub.expires_at - now).days

    return {
        "active": True,
        "plan": active_sub.plan.value,
        "expires_at": active_sub.expires_at,
        "days_left": days_left,
        "trial_available": not user.trial_used,
    }


def expire_subscriptions(db: Session) -> int:
    """Mark expired subscriptions. Called periodically by scheduler."""
    now = datetime.utcnow()
    expired = (
        db.query(Subscription)
        .filter(
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at <= now,
        )
        .all()
    )

    count = 0
    for sub in expired:
        sub.status = SubscriptionStatus.EXPIRED
        count += 1

    if count > 0:
        db.commit()
        logger.info(f"Expired {count} subscriptions")

    return count
