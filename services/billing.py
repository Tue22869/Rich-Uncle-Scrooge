"""Billing service: YooKassa + Telegram Stars payments, trial activation, subscription management."""
import os
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict

from sqlalchemy.orm import Session

from db.models import (
    User, Subscription, SubscriptionPlan, SubscriptionStatus, SubscriptionProvider,
    UsageEventKind,
)

logger = logging.getLogger(__name__)


def _log_billing_event(db: Session, user_id: int, kind: str, meta: dict) -> None:
    """Best-effort billing usage event. Never raises."""
    try:
        from services.analytics import log_event
        log_event(db, user_id=user_id, kind=kind, meta=meta)
    except Exception as e:
        logger.warning(f"billing analytics log failed: {e}")


# Approximate net RUB the developer keeps after platform fees.
# Stars: ~$0.013 net per Star at withdraw → 1 Star ≈ 1.17 RUB at 90 RUB/$.
# YooKassa: ~3.5% commission.
# Self-employed tax (6%) is applied later when computing actual income.
_NET_RUB_PER_STAR = 1.17
_YOOKASSA_NET_RATIO = 0.965


def _estimate_net_rub_stars(stars: int) -> int:
    return int(stars * _NET_RUB_PER_STAR)


def _estimate_net_rub_yookassa(rub_str: str) -> int:
    try:
        return int(float(rub_str) * _YOOKASSA_NET_RATIO)
    except (ValueError, TypeError):
        return 0

# Plan prices and durations — YooKassa (RUB)
PLANS = {
    SubscriptionPlan.MONTHLY: {"price": "190.00", "currency": "RUB", "days": 30, "label": "Месяц"},
    SubscriptionPlan.YEARLY: {"price": "1900.00", "currency": "RUB", "days": 365, "label": "Год"},
}

# Telegram Stars pricing (XTR, integer amounts). 1 Star ~ $0.013 (2026).
# Approximate parity with RUB plans; adjust as Telegram changes rates.
PLANS_STARS = {
    SubscriptionPlan.MONTHLY: {"stars": 75, "days": 30, "label": "Месяц"},
    SubscriptionPlan.YEARLY: {"stars": 750, "days": 365, "label": "Год"},
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
        provider=SubscriptionProvider.TRIAL,
        paid_at=now,
        expires_at=now + timedelta(days=14),
    )
    db.add(subscription)

    user.trial_used = True
    user.trial_activated_at = now
    db.commit()
    db.refresh(subscription)

    _log_billing_event(db, user.id, UsageEventKind.TRIAL_STARTED,
                       {"days": 14, "expires_at": subscription.expires_at.isoformat()})

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
            provider=SubscriptionProvider.YOOKASSA,
            payment_id=payment_id,
            paid_at=now,
            expires_at=start_from + timedelta(days=plan_info["days"]),
        )
        db.add(subscription)
        db.commit()
        db.refresh(subscription)

        _log_billing_event(db, user.id, UsageEventKind.SUBSCRIPTION_PAID, {
            "provider": SubscriptionProvider.YOOKASSA,
            "plan": plan.value,
            "gross_rub": float(plan_info["price"]),
            "net_rub": _estimate_net_rub_yookassa(plan_info["price"]),
            "payment_id": payment_id,
        })

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


# ---------------------------------------------------------------------------
# Telegram Stars
# ---------------------------------------------------------------------------

# Telegram limits invoice payload to 128 bytes — keep it short.
_STARS_PAYLOAD_PREFIX = "scrooge_stars"


def create_stars_invoice_payload(user_id: int, plan: SubscriptionPlan) -> str:
    """Build a compact, signed-by-format payload for a Stars invoice.

    Format: scrooge_stars:<plan>:<user_id>:<nonce>
    """
    if plan not in PLANS_STARS:
        raise ValueError(f"Stars plan {plan} not supported")
    nonce = uuid.uuid4().hex[:12]
    return f"{_STARS_PAYLOAD_PREFIX}:{plan.value}:{user_id}:{nonce}"


def parse_stars_invoice_payload(payload: str) -> Optional[Dict]:
    """Inverse of create_stars_invoice_payload. Returns dict or None if malformed."""
    if not payload or not payload.startswith(f"{_STARS_PAYLOAD_PREFIX}:"):
        return None
    parts = payload.split(":")
    if len(parts) != 4:
        return None
    _, plan_str, user_id_str, nonce = parts
    try:
        plan = SubscriptionPlan(plan_str)
    except ValueError:
        return None
    if plan not in PLANS_STARS:
        return None
    try:
        user_id = int(user_id_str)
    except ValueError:
        return None
    return {"plan": plan, "user_id": user_id, "nonce": nonce}


def confirm_stars_payment(
    db: Session,
    telegram_payment_charge_id: str,
    invoice_payload: str,
    total_amount: int,
) -> Optional[Subscription]:
    """Activate (or extend) a subscription after a successful Telegram Stars payment.

    Idempotent: a repeat call with the same telegram_payment_charge_id returns the
    existing subscription instead of creating a duplicate.
    """
    if not telegram_payment_charge_id:
        logger.error("confirm_stars_payment called without telegram_payment_charge_id")
        return None

    parsed = parse_stars_invoice_payload(invoice_payload)
    if not parsed:
        logger.error(f"Malformed Stars payload: {invoice_payload!r}")
        return None

    plan = parsed["plan"]
    user_id = parsed["user_id"]
    plan_info = PLANS_STARS[plan]

    if total_amount < plan_info["stars"]:
        logger.error(
            f"Stars payment total_amount={total_amount} below plan price {plan_info['stars']} for {plan.value}"
        )
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        logger.error(f"Stars payment for unknown user_id={user_id}")
        return None

    # Idempotency: identify by (provider, payment_id).
    existing = (
        db.query(Subscription)
        .filter(
            Subscription.provider == SubscriptionProvider.STARS,
            Subscription.payment_id == telegram_payment_charge_id,
        )
        .first()
    )
    if existing:
        logger.info(f"Stars payment {telegram_payment_charge_id} already processed (sub {existing.id})")
        return existing

    now = datetime.utcnow()

    # Extend from current expiry if user already has active subscription
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
        provider=SubscriptionProvider.STARS,
        payment_id=telegram_payment_charge_id,
        paid_at=now,
        expires_at=start_from + timedelta(days=plan_info["days"]),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    _log_billing_event(db, user.id, UsageEventKind.SUBSCRIPTION_PAID, {
        "provider": SubscriptionProvider.STARS,
        "plan": plan.value,
        "stars": total_amount,
        "net_rub": _estimate_net_rub_stars(total_amount),
        "telegram_payment_charge_id": telegram_payment_charge_id,
    })

    logger.info(
        f"Stars subscription created: id={subscription.id}, user_id={user_id}, "
        f"plan={plan.value}, charge_id={telegram_payment_charge_id}"
    )
    return subscription


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

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
