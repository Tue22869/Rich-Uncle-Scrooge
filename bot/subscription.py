"""Subscription command handlers for the Telegram bot."""
import logging
from datetime import datetime
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.models import User, SubscriptionPlan, UserSubscription, SubscriptionStatus
from db.session import SessionLocal
from payment.yookassa_service import create_payment, get_active_subscription_by_tg_id

logger = logging.getLogger(__name__)

# Callback data prefixes
PLAN_SELECT_PREFIX = "plan_select:"
CANCEL_SUB_CONFIRM = "cancel_sub_confirm"
CANCEL_SUB_ABORT = "cancel_sub_abort"


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available subscription plans with payment buttons."""
    db = SessionLocal()
    try:
        plans = (
            db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.is_active == True)
            .order_by(SubscriptionPlan.price)
            .all()
        )
        if not plans:
            await update.effective_message.reply_text("Планы подписки пока не настроены.")
            return

        # Check current subscription
        sub = get_active_subscription_by_tg_id(update.effective_user.id)

        text = "⭐ *Премиум-подписка*\n\n"
        if sub:
            days_left = (sub.end_date - datetime.utcnow()).days
            text += f"У вас активная подписка (осталось {days_left} дн.)\n"
            text += "Вы можете продлить подписку:\n\n"
        else:
            text += "Бесплатный план: базовый учёт финансов\n"
            text += "Премиум: AI-аналитика, отчёты, синхронизация с Google Sheets\n\n"

        buttons = []
        for plan in plans:
            label = f"💎 {plan.name} — {plan.price} {plan.currency} / {plan.duration_days} дн."
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"{PLAN_SELECT_PREFIX}{plan.id}")]
            )

        await update.effective_message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pricing details."""
    db = SessionLocal()
    try:
        plans = (
            db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.is_active == True)
            .order_by(SubscriptionPlan.price)
            .all()
        )
        if not plans:
            await update.effective_message.reply_text("Планы подписки пока не настроены.")
            return

        lines = ["📋 *Тарифные планы*\n"]
        lines.append("*Бесплатный:*")
        lines.append("• Учёт доходов и расходов")
        lines.append("• Счета и переводы")
        lines.append("• Базовые отчёты\n")

        for plan in plans:
            features = plan.features_json or []
            lines.append(f"*{plan.name}* — {plan.price} {plan.currency} / {plan.duration_days} дн.:")
            for feat in features:
                lines.append(f"• {feat}")
            lines.append("")

        lines.append("Оформить подписку: /subscribe")

        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def my_subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current subscription status."""
    sub = get_active_subscription_by_tg_id(update.effective_user.id)

    if not sub:
        await update.effective_message.reply_text(
            "У вас нет активной подписки.\n\n"
            "Оформить Premium: /subscribe"
        )
        return

    days_left = max(0, (sub.end_date - datetime.utcnow()).days)
    plan_name = sub.plan.name if sub.plan else "Premium"
    auto_renew = "Да" if sub.auto_renew else "Нет"

    await update.effective_message.reply_text(
        f"⭐ *Ваша подписка*\n\n"
        f"План: {plan_name}\n"
        f"Статус: Активна\n"
        f"Действует до: {sub.end_date.strftime('%d.%m.%Y')}\n"
        f"Осталось дней: {days_left}\n"
        f"Автопродление: {auto_renew}",
        parse_mode="Markdown",
    )


async def cancel_subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel auto-renewal (subscription stays active until end_date)."""
    sub = get_active_subscription_by_tg_id(update.effective_user.id)

    if not sub:
        await update.effective_message.reply_text("У вас нет активной подписки.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Да, отменить", callback_data=CANCEL_SUB_CONFIRM),
            InlineKeyboardButton("Нет", callback_data=CANCEL_SUB_ABORT),
        ]
    ])

    await update.effective_message.reply_text(
        f"Вы уверены, что хотите отменить подписку?\n"
        f"Подписка будет активна до {sub.end_date.strftime('%d.%m.%Y')}.",
        reply_markup=keyboard,
    )


async def subscription_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription-related callback queries."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith(PLAN_SELECT_PREFIX):
        await _handle_plan_selection(update, context, data)
    elif data == CANCEL_SUB_CONFIRM:
        await _handle_cancel_confirm(update, context)
    elif data == CANCEL_SUB_ABORT:
        await query.edit_message_text("Отмена подписки отменена.")


async def _handle_plan_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Process plan selection: create payment and send link."""
    query = update.callback_query
    plan_id_str = data.replace(PLAN_SELECT_PREFIX, "")

    try:
        plan_id = int(plan_id_str)
    except ValueError:
        await query.edit_message_text("Ошибка: неверный план.")
        return

    db = SessionLocal()
    try:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
        if not plan:
            await query.edit_message_text("План не найден.")
            return

        user = db.query(User).filter(User.tg_user_id == update.effective_user.id).first()
        if not user:
            await query.edit_message_text("Сначала начните работу: /start")
            return

        payment_url, error = create_payment(
            user_id=user.id,
            plan_id=plan.id,
            amount=plan.price,
            currency=plan.currency,
            description=f"Подписка «{plan.name}» — {plan.duration_days} дн.",
        )

        if error:
            logger.error("Payment creation error: %s", error)
            await query.edit_message_text(
                "Не удалось создать платёж. Попробуйте позже."
            )
            return

        await query.edit_message_text(
            f"💳 *Оплата подписки «{plan.name}»*\n\n"
            f"Сумма: {plan.price} {plan.currency}\n"
            f"Срок: {plan.duration_days} дн.\n\n"
            f"[Перейти к оплате]({payment_url})\n\n"
            "После оплаты подписка активируется автоматически.",
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def _handle_cancel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm subscription cancellation."""
    query = update.callback_query
    tg_user_id = update.effective_user.id

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Пользователь не найден.")
            return

        sub = (
            db.query(UserSubscription)
            .filter(
                UserSubscription.user_id == user.id,
                UserSubscription.status == SubscriptionStatus.ACTIVE,
            )
            .first()
        )

        if not sub:
            await query.edit_message_text("Активная подписка не найдена.")
            return

        sub.auto_renew = False
        sub.status = SubscriptionStatus.CANCELLED
        db.commit()

        await query.edit_message_text(
            f"Подписка отменена.\n"
            f"Вы можете пользоваться Premium до {sub.end_date.strftime('%d.%m.%Y')}."
        )
    finally:
        db.close()
