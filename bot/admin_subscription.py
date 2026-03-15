"""Admin commands for managing subscriptions."""
import logging
import os
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from db.models import User, UserSubscription, SubscriptionStatus, SubscriptionPlan
from db.session import SessionLocal

logger = logging.getLogger(__name__)

ADMIN_USER_IDS = set()
_raw = os.getenv("ADMIN_USER_IDS", "")
if _raw:
    ADMIN_USER_IDS = {int(x.strip()) for x in _raw.split(",") if x.strip().isdigit()}


def _is_admin(tg_user_id: int) -> bool:
    return tg_user_id in ADMIN_USER_IDS


async def grant_subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /grant_subscription USER_TG_ID DAYS"""
    if not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("У вас нет прав для этой команды.")
        return

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Использование: /grant_subscription <tg_user_id> <days>"
        )
        return

    try:
        target_tg_id = int(args[0])
        days = int(args[1])
    except ValueError:
        await update.effective_message.reply_text("Неверные аргументы. Ожидаются числа.")
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == target_tg_id).first()
        if not user:
            await update.effective_message.reply_text(f"Пользователь {target_tg_id} не найден.")
            return

        # Find default premium plan or first active plan
        plan = (
            db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.is_active == True)
            .order_by(SubscriptionPlan.price.desc())
            .first()
        )

        now = datetime.utcnow()

        # Extend existing or create new
        existing = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user.id,
                UserSubscription.status == SubscriptionStatus.ACTIVE,
                UserSubscription.end_date > now,
            )
            .first()
        )

        if existing:
            existing.end_date = existing.end_date + timedelta(days=days)
            db.commit()
            await update.effective_message.reply_text(
                f"Подписка продлена для {target_tg_id} до {existing.end_date.strftime('%d.%m.%Y')}."
            )
        else:
            sub = UserSubscription(
                user_id=user.id,
                plan_id=plan.id if plan else None,
                start_date=now,
                end_date=now + timedelta(days=days),
                status=SubscriptionStatus.ACTIVE,
            )
            db.add(sub)
            db.commit()
            await update.effective_message.reply_text(
                f"Подписка выдана для {target_tg_id} на {days} дн. (до {sub.end_date.strftime('%d.%m.%Y')})."
            )

        # Notify the user
        try:
            await context.bot.send_message(
                chat_id=target_tg_id,
                text=f"🎁 Вам выдана Premium-подписка на {days} дн.!\n"
                     f"Проверьте статус: /mysubscription",
            )
        except Exception as e:
            logger.warning("Could not notify user %s: %s", target_tg_id, e)

    finally:
        db.close()


async def subscriptions_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /subscriptions_list — list active subscriptions."""
    if not _is_admin(update.effective_user.id):
        await update.effective_message.reply_text("У вас нет прав для этой команды.")
        return

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        subs = (
            db.query(UserSubscription)
            .join(User)
            .filter(
                UserSubscription.status == SubscriptionStatus.ACTIVE,
                UserSubscription.end_date > now,
            )
            .order_by(UserSubscription.end_date)
            .limit(50)
            .all()
        )

        if not subs:
            await update.effective_message.reply_text("Нет активных подписок.")
            return

        lines = [f"📊 Активные подписки ({len(subs)}):\n"]
        for sub in subs:
            days_left = (sub.end_date - now).days
            plan_name = sub.plan.name if sub.plan else "—"
            lines.append(
                f"• tg:{sub.user.tg_user_id} | {plan_name} | "
                f"до {sub.end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
            )

        await update.effective_message.reply_text("\n".join(lines))
    finally:
        db.close()
