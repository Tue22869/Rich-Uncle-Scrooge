"""YooKassa payment service — create payments, process webhooks, manage subscriptions."""
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from yookassa import Payment as YooPayment
from yookassa.domain.notification import WebhookNotification, WebhookNotificationEventType

from db.models import (
    Payment, PaymentStatus, SubscriptionPlan, UserSubscription, SubscriptionStatus, User,
)
from db.session import SessionLocal
from payment.config import PAYMENT_RETURN_URL

logger = logging.getLogger(__name__)


def create_payment(
    user_id: int,
    plan_id: int,
    amount: Decimal,
    currency: str,
    description: str,
) -> tuple[str | None, str | None]:
    """Create a YooKassa payment and store it in DB.

    Returns (payment_url, error_message).
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None, "User not found"

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
        if not plan:
            return None, "Plan not found"

        idempotency_key = str(uuid.uuid4())

        yoo_payment = YooPayment.create(
            {
                "amount": {
                    "value": str(amount),
                    "currency": currency,
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": PAYMENT_RETURN_URL,
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "user_id": str(user.id),
                    "tg_user_id": str(user.tg_user_id),
                    "plan_id": str(plan_id),
                },
            },
            idempotency_key=idempotency_key,
        )

        db_payment = Payment(
            user_id=user.id,
            amount=amount,
            currency=currency,
            yookassa_payment_id=yoo_payment.id,
            status=PaymentStatus.PENDING,
        )
        db.add(db_payment)
        db.commit()

        payment_url = yoo_payment.confirmation.confirmation_url
        logger.info(
            "Payment created: yookassa_id=%s user=%s plan=%s amount=%s %s",
            yoo_payment.id, user.tg_user_id, plan.name, amount, currency,
        )
        return payment_url, None

    except Exception as e:
        db.rollback()
        logger.error("Failed to create payment: %s", e, exc_info=True)
        return None, str(e)
    finally:
        db.close()


def process_webhook(body: dict) -> tuple[int | None, str | None]:
    """Process a YooKassa webhook notification.

    Returns (tg_user_id, event_type) on success for Telegram notification,
    or (None, None) if skipped/failed.
    """
    db = SessionLocal()
    try:
        notification = WebhookNotification(body)
        payment_obj = notification.object
        yookassa_id = payment_obj.id
        event = notification.event

        logger.info("Webhook received: event=%s yookassa_id=%s", event, yookassa_id)

        db_payment = (
            db.query(Payment)
            .filter(Payment.yookassa_payment_id == yookassa_id)
            .first()
        )

        if not db_payment:
            logger.warning("Payment not found in DB: %s", yookassa_id)
            return None, None

        # Idempotency: skip if already in a final state
        if db_payment.status in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED):
            logger.info("Payment %s already in final state %s, skipping", yookassa_id, db_payment.status.value)
            return None, None

        user = db.query(User).filter(User.id == db_payment.user_id).first()
        if not user:
            logger.error("User not found for payment %s", yookassa_id)
            return None, None

        if event == WebhookNotificationEventType.PAYMENT_SUCCEEDED:
            db_payment.status = PaymentStatus.SUCCEEDED
            db_payment.updated_at = datetime.utcnow()

            # Extract plan_id from metadata
            metadata = payment_obj.metadata or {}
            plan_id = metadata.get("plan_id")
            if plan_id:
                plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == int(plan_id)).first()
                if plan:
                    subscription = _activate_subscription(db, user.id, plan)
                    db_payment.subscription_id = subscription.id

            db.commit()
            logger.info("Payment succeeded: %s for user tg_id=%s", yookassa_id, user.tg_user_id)
            return user.tg_user_id, event

        elif event == WebhookNotificationEventType.PAYMENT_CANCELED:
            db_payment.status = PaymentStatus.CANCELLED
            db_payment.updated_at = datetime.utcnow()
            db.commit()
            logger.info("Payment cancelled: %s", yookassa_id)
            return user.tg_user_id, event

        return None, None

    except Exception as e:
        db.rollback()
        logger.error("Webhook processing error: %s", e, exc_info=True)
        return None, None
    finally:
        db.close()


def _activate_subscription(db, user_id: int, plan: SubscriptionPlan) -> UserSubscription:
    """Create or extend a user subscription."""
    now = datetime.utcnow()

    # Check for existing active subscription to extend
    existing = (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SubscriptionStatus.ACTIVE,
            UserSubscription.end_date > now,
        )
        .first()
    )

    if existing:
        # Extend from current end date
        existing.end_date = existing.end_date + timedelta(days=plan.duration_days)
        existing.plan_id = plan.id
        logger.info("Subscription extended for user %s until %s", user_id, existing.end_date)
        return existing
    else:
        subscription = UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            start_date=now,
            end_date=now + timedelta(days=plan.duration_days),
            status=SubscriptionStatus.ACTIVE,
        )
        db.add(subscription)
        db.flush()
        logger.info("New subscription created for user %s until %s", user_id, subscription.end_date)
        return subscription


def get_active_subscription(user_id: int) -> UserSubscription | None:
    """Get the user's active subscription, auto-expiring if past end_date."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        sub = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user_id,
                UserSubscription.status == SubscriptionStatus.ACTIVE,
            )
            .order_by(UserSubscription.end_date.desc())
            .first()
        )
        if sub and sub.end_date <= now:
            sub.status = SubscriptionStatus.EXPIRED
            db.commit()
            return None
        return sub
    finally:
        db.close()


def get_active_subscription_by_tg_id(tg_user_id: int) -> UserSubscription | None:
    """Get active subscription by Telegram user ID."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            return None
        now = datetime.utcnow()
        sub = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user.id,
                UserSubscription.status == SubscriptionStatus.ACTIVE,
            )
            .order_by(UserSubscription.end_date.desc())
            .first()
        )
        if sub and sub.end_date <= now:
            sub.status = SubscriptionStatus.EXPIRED
            db.commit()
            return None
        return sub
    finally:
        db.close()
