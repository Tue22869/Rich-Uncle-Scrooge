"""Middleware: subscription checks, usage stats, paywall display."""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.orm import Session

from db.models import User, Subscription, SubscriptionStatus

logger = logging.getLogger(__name__)


def _admin_ids() -> set[int]:
    """Parse ADMIN_USER_IDS env var (comma-separated Telegram user IDs)."""
    raw = os.getenv("ADMIN_USER_IDS", "")
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}


def _has_premium_access(db: Session, user: User) -> bool:
    """Check if user has an active subscription (trial, monthly, or yearly).

    Admins listed in ADMIN_USER_IDS always have access — they own the bot
    and should not be paying their own paywall.
    """
    if user.tg_user_id in _admin_ids():
        return True
    now = datetime.utcnow()
    active_sub = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        )
        .first()
    )
    return active_sub is not None


def _get_active_subscription(db: Session, user: User) -> Optional[Subscription]:
    """Get the current active subscription (if any)."""
    now = datetime.utcnow()
    return (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
        .first()
    )


def _get_trial_days_left(user: User) -> Optional[int]:
    """Return remaining trial days, or None if no trial active."""
    if not user.trial_activated_at:
        return None
    trial_end = user.trial_activated_at + timedelta(days=14)
    now = datetime.utcnow()
    if now >= trial_end:
        return 0
    return (trial_end - now).days


def is_pro(db: Session, user_id: int) -> bool:
    """Public helper: check if a tg_user_id has premium access."""
    user = db.query(User).filter(User.tg_user_id == user_id).first()
    if not user:
        return False
    return _has_premium_access(db, user)


def get_usage_stats(tg_user_id: int) -> Dict:
    """Get subscription/usage stats for display."""
    from db.session import SessionLocal
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            return {"is_premium": False}

        premium = _has_premium_access(db, user)
        return {"is_premium": premium}
    finally:
        db.close()


async def check_subscription(db: Session, user: User) -> Tuple[bool, Optional[str], Optional[InlineKeyboardMarkup]]:
    """
    Middleware check before every user action (except /start, payment, trial activation).
    Returns (allowed, paywall_text, paywall_keyboard).
    If allowed is True, proceed normally.
    """
    if _has_premium_access(db, user):
        return True, None, None

    # User has no active subscription — show paywall
    trial_available = not user.trial_used

    text = (
        "💎 *Для использования бота нужна подписка*\n\n"
        "🔹 *Что включает Premium:*\n"
        "• Запись доходов/расходов/переводов\n"
        "• Управление счетами\n"
        "• Отчёты за любой период\n"
        "• AI-аналитика без ограничений\n"
        "• Еженедельные и ежемесячные дайджесты\n"
        "• Бюджеты по категориям\n"
        "• Google Sheets синхронизация\n"
        "• Стрики, ачивки, напоминания\n"
    )

    buttons = []

    if trial_available:
        text += "\n🎁 Попробуйте бесплатно 14 дней!\n"
        buttons.append([InlineKeyboardButton("🎁 Пробный период (14 дней)", callback_data="sub:activate_trial")])

    buttons.extend([
        [
            InlineKeyboardButton("📅 Месяц — 190₽", callback_data="sub:buy:monthly"),
            InlineKeyboardButton("📆 Год — 1900₽", callback_data="sub:buy:yearly"),
        ],
        [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
    ])

    return False, text, InlineKeyboardMarkup(buttons)
