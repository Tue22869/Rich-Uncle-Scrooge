"""Subscription handlers: premium menu, trial activation, payment callbacks."""
import os
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes

from db.models import SubscriptionPlan
from db.session import SessionLocal
from services.ledger import get_or_create_user
from services.billing import (
    activate_trial, create_payment, check_payment_status,
    confirm_payment, get_subscription_info, PLANS, PLANS_STARS,
    create_stars_invoice_payload,
)


def _yookassa_enabled() -> bool:
    """Show YooKassa as fully working only when a real shop is configured."""
    return bool(os.getenv("YOOKASSA_SHOP_ID") and os.getenv("YOOKASSA_SECRET_KEY"))

logger = logging.getLogger(__name__)


async def _show_premium_menu(update: Update, edit_message: bool = False):
    """Show premium/subscription information screen."""
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        info = get_subscription_info(db, user)

        stars_monthly = PLANS_STARS[SubscriptionPlan.MONTHLY]["stars"]
        stars_yearly = PLANS_STARS[SubscriptionPlan.YEARLY]["stars"]

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
                    InlineKeyboardButton(f"⭐ Месяц — {stars_monthly} Stars", callback_data="sub:buy_stars:monthly"),
                    InlineKeyboardButton(f"⭐ Год — {stars_yearly} Stars", callback_data="sub:buy_stars:yearly"),
                ])
                buttons.append([
                    InlineKeyboardButton("💳 Месяц — 190₽", callback_data="sub:buy:monthly"),
                    InlineKeyboardButton("💳 Год — 1900₽", callback_data="sub:buy:yearly"),
                ])
            else:
                buttons.append([
                    InlineKeyboardButton("⭐ Продлить за Stars", callback_data="sub:buy_stars:" + info["plan"]),
                ])
                buttons.append([
                    InlineKeyboardButton("💳 Продлить картой", callback_data="sub:buy:" + info["plan"]),
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
                f"⭐ Месяц — {stars_monthly} Stars\n"
                f"⭐ Год — {stars_yearly} Stars (выгода ~17%)\n"
            )
            text += "\nИли картой:\n📅 Месяц — 190₽\n📆 Год — 1900₽\n"

            buttons = []
            if info.get("trial_available"):
                text += "\n🎁 Попробуйте бесплатно 14 дней!\n"
                buttons.append([
                    InlineKeyboardButton("🎁 Пробный период (14 дней)", callback_data="sub:activate_trial"),
                ])

            buttons.append([
                InlineKeyboardButton(f"⭐ Месяц — {stars_monthly}", callback_data="sub:buy_stars:monthly"),
                InlineKeyboardButton(f"⭐ Год — {stars_yearly}", callback_data="sub:buy_stars:yearly"),
            ])
            buttons.append([
                InlineKeyboardButton("💳 Месяц — 190₽", callback_data="sub:buy:monthly"),
                InlineKeyboardButton("💳 Год — 1900₽", callback_data="sub:buy:yearly"),
            ])
            buttons.append([InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")])

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
    elif data.startswith("sub:buy_stars:"):
        plan_str = data.replace("sub:buy_stars:", "")
        await _handle_buy_stars(update, context, plan_str)
    elif data.startswith("sub:buy:"):
        plan_str = data.replace("sub:buy:", "")
        if not _yookassa_enabled():
            await query.edit_message_text(
                "🛠 *Оплата картой (ЮKassa) ещё не подключена.*\n\n"
                "Заявка на самозанятость подана — как только ЮKassa включит магазин, кнопка заработает сама.\n\n"
                "Пока можно оформить подписку через Telegram Stars ⭐.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐ Оплатить Stars", callback_data="menu:premium")],
                    [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
                ]),
                parse_mode="Markdown",
            )
            return
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


async def _handle_buy_stars(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_str: str):
    """Send a Telegram Stars invoice for the selected plan."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    plan_map = {"monthly": SubscriptionPlan.MONTHLY, "yearly": SubscriptionPlan.YEARLY}
    plan = plan_map.get(plan_str)
    if not plan:
        await query.edit_message_text("❌ Неизвестный тариф.")
        return

    plan_info = PLANS_STARS[plan]

    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        payload = create_stars_invoice_payload(user.id, plan)
    finally:
        db.close()

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=f"Premium «Дядя Скрудж» — {plan_info['label']}",
            description=(
                "Полный доступ ко всем возможностям: запись доходов/расходов, "
                "отчёты, AI-аналитика, бюджеты, голосовой ввод, Google Sheets."
            ),
            payload=payload,
            provider_token="",  # empty for Telegram Stars (XTR)
            currency="XTR",
            prices=[LabeledPrice(label=plan_info["label"], amount=plan_info["stars"])],
        )
        # Acknowledge in the original message — the invoice arrives as a separate message.
        await query.edit_message_text(
            f"⭐ Счёт на оплату отправлен — {plan_info['stars']} Stars за «{plan_info['label']}».\n"
            f"Оплатите его в Telegram, подписка активируется автоматически.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
            ]),
        )
    except Exception as e:
        logger.error(f"Stars invoice send error: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Не удалось создать счёт. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
            ]),
        )


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
