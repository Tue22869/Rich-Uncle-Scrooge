"""Subscription handlers: premium menu, trial activation, payment callbacks."""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.models import SubscriptionPlan
from db.session import SessionLocal
from services.ledger import get_or_create_user
from services.billing import (
    activate_trial, create_payment, check_payment_status,
    confirm_payment, get_subscription_info, PLANS,
)

logger = logging.getLogger(__name__)


async def _show_premium_menu(update: Update, edit_message: bool = False):
    """Show premium/subscription information screen."""
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        info = get_subscription_info(db, user)

        if info["active"]:
            plan_labels = {"trial": "Пробный период", "monthly": "Месяц", "yearly": "Год"}
            plan_label = plan_labels.get(info["plan"], info["plan"])
            expires = info["expires_at"].strftime("%d.%m.%Y") if info["expires_at"] else "—"
            days_left = info.get("days_left", 0)

            text = (
                "💎 *Premium — активна*\n\n"
                f"📋 Тариф: {plan_label}\n"
                f"📅 Действует до: {expires}\n"
                f"⏳ Осталось: {days_left} дн.\n\n"
                "✨ Вам доступны все возможности бота!"
            )

            buttons = []
            if info["plan"] == "trial":
                buttons.append([
                    InlineKeyboardButton("📅 Месяц — 190₽", callback_data="sub:buy:monthly"),
                    InlineKeyboardButton("📆 Год — 1900₽", callback_data="sub:buy:yearly"),
                ])
            else:
                buttons.append([
                    InlineKeyboardButton("🔄 Продлить", callback_data="sub:buy:" + info["plan"]),
                ])
            buttons.append([InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")])
        else:
            text = (
                "💎 *Premium*\n\n"
                "🔹 *Все возможности бота:*\n"
                "• Запись доходов/расходов/переводов\n"
                "• Мультивалютные счета\n"
                "• Отчёты за любой период\n"
                "• AI-аналитика без ограничений\n"
                "• Еженедельные и ежемесячные дайджесты\n"
                "• Бюджеты по категориям\n"
                "• Google Sheets синхронизация\n"
                "• Стрики и ачивки\n"
                "• Голосовой ввод\n\n"
                "🔹 *Тарифы:*\n"
                "📅 Месяц — 190₽\n"
                "📆 Год — 1900₽ (выгода ~17%)\n"
            )

            buttons = []
            if info.get("trial_available"):
                text += "\n🎁 Попробуйте бесплатно 14 дней!\n"
                buttons.append([
                    InlineKeyboardButton("🎁 Пробный период (14 дней)", callback_data="sub:activate_trial"),
                ])

            buttons.extend([
                [
                    InlineKeyboardButton("📅 Месяц — 190₽", callback_data="sub:buy:monthly"),
                    InlineKeyboardButton("📆 Год — 1900₽", callback_data="sub:buy:yearly"),
                ],
                [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
            ])

        keyboard = InlineKeyboardMarkup(buttons)

        target = update.callback_query if edit_message and update.callback_query else None
        if target:
            await target.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    finally:
        db.close()


async def _show_status(update: Update, edit_message: bool = False):
    """Show subscription status (alias for premium menu)."""
    await _show_premium_menu(update, edit_message=edit_message)


async def subscription_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription-related callbacks (sub:*)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "sub:activate_trial":
        await _handle_activate_trial(update, context)
    elif data.startswith("sub:buy:"):
        plan_str = data.replace("sub:buy:", "")
        await _handle_buy(update, context, plan_str)
    elif data.startswith("sub:check:"):
        payment_id = data.replace("sub:check:", "")
        await _handle_check_payment(update, context, payment_id)


async def _handle_activate_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate free trial for user."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)

        if user.trial_used:
            await query.edit_message_text(
                "❌ Пробный период уже был использован.\n\n"
                "Оформите подписку для доступа ко всем возможностям:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📅 Месяц — 190₽", callback_data="sub:buy:monthly"),
                        InlineKeyboardButton("📆 Год — 1900₽", callback_data="sub:buy:yearly"),
                    ],
                    [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
                ]),
            )
            return

        sub = activate_trial(db, user.id)
        if sub:
            expires = sub.expires_at.strftime("%d.%m.%Y")
            await query.edit_message_text(
                f"🎉 *Пробный период активирован!*\n\n"
                f"Вам доступны все возможности бота на 14 дней.\n"
                f"📅 Действует до: {expires}\n\n"
                "Просто начните записывать расходы и доходы!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
                ]),
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text("❌ Не удалось активировать пробный период.")
    except Exception as e:
        logger.error(f"Trial activation error: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации пробного периода.")
    finally:
        db.close()


async def _handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_str: str):
    """Create YooKassa payment and show payment link."""
    query = update.callback_query
    tg_user_id = update.effective_user.id

    plan_map = {"monthly": SubscriptionPlan.MONTHLY, "yearly": SubscriptionPlan.YEARLY}
    plan = plan_map.get(plan_str)
    if not plan:
        await query.edit_message_text("❌ Неизвестный тариф.")
        return

    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        result = create_payment(db, user.id, plan)

        if not result or not result.get("confirmation_url"):
            await query.edit_message_text(
                "❌ Не удалось создать платёж.\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
                ]),
            )
            return

        plan_info = PLANS[plan]
        payment_id = result["payment_id"]

        await query.edit_message_text(
            f"💳 *Оплата подписки — {plan_info['label']}*\n\n"
            f"💰 Сумма: {plan_info['price']} ₽\n\n"
            f"Нажмите кнопку ниже для перехода к оплате.\n"
            f"После оплаты нажмите «Проверить оплату».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Перейти к оплате", url=result["confirmation_url"])],
                [InlineKeyboardButton("✅ Проверить оплату", callback_data=f"sub:check:{payment_id}")],
                [InlineKeyboardButton("↩️ Отмена", callback_data="menu:premium")],
            ]),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Payment creation error: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при создании платежа.")
    finally:
        db.close()


async def _handle_check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id: str):
    """Check payment status and activate subscription if paid."""
    query = update.callback_query
    db = SessionLocal()
    try:
        status = check_payment_status(payment_id)

        if status == "succeeded":
            sub = confirm_payment(db, payment_id)
            if sub:
                expires = sub.expires_at.strftime("%d.%m.%Y")
                plan_labels = {"monthly": "Месяц", "yearly": "Год"}
                plan_label = plan_labels.get(sub.plan.value, sub.plan.value)

                await query.edit_message_text(
                    f"🎉 *Подписка оформлена!*\n\n"
                    f"📋 Тариф: {plan_label}\n"
                    f"📅 Действует до: {expires}\n\n"
                    "✨ Все возможности бота теперь доступны!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
                    ]),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    "⚠️ Платёж прошёл, но произошла ошибка активации.\n"
                    "Обратитесь в поддержку с ID платежа.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
                    ]),
                )

        elif status == "pending" or status == "waiting_for_capture":
            await query.answer("⏳ Платёж ещё обрабатывается. Попробуйте через минуту.", show_alert=True)

        elif status == "canceled":
            await query.edit_message_text(
                "❌ Платёж отменён.\n\nПопробуйте ещё раз:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 Premium", callback_data="menu:premium")],
                    [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
                ]),
            )
        else:
            await query.answer(f"Статус: {status or 'неизвестен'}. Попробуйте позже.", show_alert=True)

    except Exception as e:
        logger.error(f"Payment check error: {e}", exc_info=True)
        await query.answer("❌ Ошибка при проверке платежа.", show_alert=True)
    finally:
        db.close()
