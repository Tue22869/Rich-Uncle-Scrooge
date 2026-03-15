"""Subscription middleware — decorator and expiration checks."""
import logging
from datetime import datetime, timedelta
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from db.models import User, UserSubscription, SubscriptionStatus
from db.session import SessionLocal

logger = logging.getLogger(__name__)


def subscription_required(func):
    """Decorator that restricts a handler to users with an active premium subscription."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        tg_user_id = update.effective_user.id
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
            if not user:
                await update.effective_message.reply_text(
                    "Сначала начните работу с ботом: /start"
                )
                return

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
                sub = None

            if not sub:
                await update.effective_message.reply_text(
                    "⭐ Эта функция доступна только с Premium-подпиской.\n\n"
                    "Используйте /subscribe чтобы оформить подписку."
                )
                return

        finally:
            db.close()

        return await func(update, context)

    return wrapper


async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: send reminders for subscriptions expiring in 3 days and expire old ones."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        three_days = now + timedelta(days=3)

        # Find subscriptions expiring within 3 days
        expiring = (
            db.query(UserSubscription)
            .join(User)
            .filter(
                UserSubscription.status == SubscriptionStatus.ACTIVE,
                UserSubscription.end_date > now,
                UserSubscription.end_date <= three_days,
            )
            .all()
        )

        for sub in expiring:
            days_left = (sub.end_date - now).days
            try:
                await context.bot.send_message(
                    chat_id=sub.user.tg_user_id,
                    text=(
                        f"⏰ Ваша Premium-подписка истекает через {days_left} дн.\n\n"
                        "Продлите подписку: /subscribe"
                    ),
                )
            except Exception as e:
                logger.error("Failed to send expiry reminder to %s: %s", sub.user.tg_user_id, e)

        # Auto-expire past-due subscriptions
        expired = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.status == SubscriptionStatus.ACTIVE,
                UserSubscription.end_date <= now,
            )
            .all()
        )
        for sub in expired:
            sub.status = SubscriptionStatus.EXPIRED
        if expired:
            db.commit()
            logger.info("Auto-expired %d subscriptions", len(expired))

    except Exception as e:
        logger.error("Expiration check error: %s", e)
    finally:
        db.close()
