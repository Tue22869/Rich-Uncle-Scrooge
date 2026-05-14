"""Menu handlers — main menu, reports menu, settings, navigation."""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from db.models import User, Account
from db.session import SessionLocal
from bot.middleware import _has_premium_access, _get_trial_days_left
from services.ledger import get_or_create_user
from utils.money import format_amount

logger = logging.getLogger(__name__)

# Persistent bottom keyboard (ReplyKeyboardMarkup) — always visible
PERSISTENT_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("💰 Счета"), KeyboardButton("📊 Отчёты")],
        [KeyboardButton("📝 Записать"), KeyboardButton("⚙️ Настройки")],
        [KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Text labels for persistent keyboard routing
PERSISTENT_LABELS = {
    "💰 Счета", "📊 Отчёты", "📝 Записать", "⚙️ Настройки", "❓ Помощь",
}

# Callback data for menu navigation
MENU_MAIN = "menu:main"
MENU_FINANCES = "menu:finances"
MENU_REPORTS = "menu:reports"
MENU_PREMIUM = "menu:premium"
MENU_HELP = "menu:help"
MENU_SETTINGS = "menu:settings"
MENU_ABOUT = "menu:about"
MENU_STATUS = "menu:status"


async def handle_persistent_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle persistent keyboard button presses. Returns True if handled."""
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip()
    if text not in PERSISTENT_LABELS:
        return False

    db = SessionLocal()
    try:
        tg_user_id = update.effective_user.id
        user = get_or_create_user(db, tg_user_id)

        # Check subscription for all persistent menu actions
        from bot.middleware import check_subscription
        allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
        if not allowed:
            await update.message.reply_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
            return True

        if text == "💰 Счета":
            await _show_accounts_screen(update, db=db, user=user, edit_message=False)

        elif text == "📊 Отчёты":
            usage_line = "📊 Отчёты: безлимитные ✨"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📊 За сегодня", callback_data="report:today"),
                    InlineKeyboardButton("📅 За неделю", callback_data="report:week"),
                ],
                [
                    InlineKeyboardButton("🗓 За месяц", callback_data="report:month"),
                    InlineKeyboardButton("🗓 Прошлый месяц", callback_data="report:last_month"),
                ],
                [InlineKeyboardButton("📆 За год", callback_data="report:year")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
            ])
            await update.message.reply_text(
                f"📈 *Отчёты*\n\n{usage_line}\n\nВыберите период:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

        elif text == "📝 Записать":
            await update.message.reply_text(
                "📝 *Записать операцию*\n\n"
                "Просто напишите текстом:\n"
                "• «кофе 320» — расход\n"
                "• «+50000 зп» — доход\n"
                "• «переведи 10к с карты на нал» — перевод\n"
                "• «бюджет на кофе 3000» — установить бюджет\n\n"
                "Или отправьте голосовое сообщение.",
                parse_mode="Markdown",
            )

        elif text == "⚙️ Настройки":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🕐 Таймзона", callback_data="settings:timezone")],
                [InlineKeyboardButton("📊 Google Sheets", callback_data="cmd:sheets")],
                [InlineKeyboardButton("💎 Подписка", callback_data=MENU_PREMIUM)],
                [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
            ])
            await update.message.reply_text(
                f"⚙️ *Настройки*\n\n🕐 Таймзона: {user.timezone}",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

        elif text == "❓ Помощь":
            from bot.handlers import HELP_INTRO, _help_sections_keyboard
            await update.message.reply_text(
                HELP_INTRO,
                reply_markup=_help_sections_keyboard(),
                parse_mode="Markdown",
            )

        return True
    finally:
        db.close()


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu (triggered by /menu)."""
    await _show_main_menu(update)


async def _show_main_menu(update: Update, edit_message: bool = False):
    """Render the main menu with inline buttons."""
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        has_premium = _has_premium_access(db, user)
        trial_days = _get_trial_days_left(user)

        status_line = ""
        if has_premium and trial_days is None:
            status_line = "💎 Premium"
        elif trial_days is not None and trial_days > 0:
            status_line = f"🎁 Пробный период ({trial_days} дн.)"
        else:
            status_line = "📱 Подписка не активна"

        text = (
            "🏠 *Главное меню*\n\n"
            f"💰 Дядя Скрудж — ваш финансовый помощник\n"
            f"📌 {status_line}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💰 Счета", callback_data="cmd:accounts"),
                InlineKeyboardButton("📈 Отчёты", callback_data=MENU_REPORTS),
            ],
            [
                InlineKeyboardButton("📊 Мои финансы", callback_data=MENU_FINANCES),
                InlineKeyboardButton("📄 Google Sheets", callback_data="cmd:sheets"),
            ],
            [
                InlineKeyboardButton("💎 Premium", callback_data=MENU_PREMIUM),
                InlineKeyboardButton("❓ Помощь", callback_data=MENU_HELP),
            ],
            [
                InlineKeyboardButton("⚙️ Настройки", callback_data=MENU_SETTINGS),
                InlineKeyboardButton("📱 О боте", callback_data=MENU_ABOUT),
            ],
        ])

        target = update.callback_query if edit_message and update.callback_query else None
        if target:
            await target.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            # Send inline menu with persistent keyboard attached
            # (Telegram shows ReplyKeyboard and inline buttons can't coexist on same message,
            # so we send the persistent keyboard silently on the main text)
            await update.effective_message.reply_text(
                text, reply_markup=PERSISTENT_MENU, parse_mode="Markdown"
            )
            # Then send inline buttons as a follow-up
            await update.effective_message.reply_text(
                "👇 Быстрые действия:", reply_markup=keyboard
            )
    finally:
        db.close()


async def _show_finances_menu(update: Update):
    """Show finances dashboard: totals by currency + shortcuts (no duplicate list of accounts)."""
    from decimal import Decimal

    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начни работу: /start")
            return

        accounts = db.query(Account).filter(Account.user_id == user.id).all()

        if not accounts:
            text = (
                "📊 *Мои финансы*\n\n"
                "У тебя пока нет счетов.\n\n"
                "Чтобы создать первый — открой `💰 Счета` снизу и нажми «➕ Создать счёт»."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Счета", callback_data="cmd:accounts")],
                [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
            ])
        else:
            totals: dict[str, Decimal] = {}
            for acc in accounts:
                totals.setdefault(acc.currency, Decimal("0"))
                totals[acc.currency] += acc.balance

            lines = ["📊 *Мои финансы*\n", "Итого по валютам:"]
            for currency, total in totals.items():
                lines.append(f"  {format_amount(total, currency)}")
            lines.append(f"\nСчетов: {len(accounts)}")
            text = "\n".join(lines)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💰 Счета", callback_data="cmd:accounts"),
                    InlineKeyboardButton("📜 История", callback_data="cmd:history"),
                ],
                [
                    InlineKeyboardButton("📄 Google Sheets", callback_data="cmd:sheets"),
                    InlineKeyboardButton("📈 Отчёты", callback_data=MENU_REPORTS),
                ],
                [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
            ])

        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    finally:
        db.close()


async def _show_reports_menu(update: Update):
    """Show reports menu with usage stats."""
    query = update.callback_query
    usage_line = "📊 Отчёты: безлимитные ✨"

    text = (
        "📈 *Отчёты*\n\n"
        f"{usage_line}\n\n"
        "Выберите период или напишите:\n"
        "«отчет за ноябрь», «статистика за неделю»"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 За сегодня", callback_data="report:today"),
            InlineKeyboardButton("📅 За неделю", callback_data="report:week"),
        ],
        [
            InlineKeyboardButton("🗓 За месяц", callback_data="report:month"),
            InlineKeyboardButton("🗓 Прошлый месяц", callback_data="report:last_month"),
        ],
        [InlineKeyboardButton("📆 За год", callback_data="report:year")],
        [
            InlineKeyboardButton("📜 Последние операции", callback_data="cmd:history"),
            InlineKeyboardButton("🔍 Аналитика", callback_data="cmd:analytics"),
        ],
        [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_help_menu(update: Update):
    """Show help entry (delegates to sectioned help)."""
    from bot.handlers import HELP_INTRO, _help_sections_keyboard

    query = update.callback_query
    await query.edit_message_text(
        HELP_INTRO,
        reply_markup=_help_sections_keyboard(),
        parse_mode="Markdown",
    )


async def _show_settings_menu(update: Update):
    """Show settings menu."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        tz_display = user.timezone if user else "Europe/London"

        text = (
            "⚙️ *Настройки*\n\n"
            f"🕐 Таймзона: {tz_display}\n\n"
            "📋 Управление счетами:\n"
            "• «создай счет ...» или «удали счет ...»\n"
            "• «главный счет карта»"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🕐 Таймзона", callback_data="settings:timezone")],
            [InlineKeyboardButton("📊 Google Sheets", callback_data="cmd:sheets")],
            [InlineKeyboardButton("💎 Подписка", callback_data=MENU_PREMIUM)],
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])

        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    finally:
        db.close()


async def _show_about(update: Update):
    """Show about info."""
    query = update.callback_query
    text = (
        "📱 *О боте «Дядя Скрудж»*\n\n"
        "💰 Умный помощник для учёта личных финансов прямо в Telegram.\n\n"
        "🔹 *Возможности:*\n"
        "🤖 Понимание естественного языка (GPT)\n"
        "🎤 Голосовой ввод расходов\n"
        "💳 Мультивалютные счета и переводы\n"
        "📊 Отчёты по категориям и периодам\n"
        "🔍 AI-аналитика: «почему много на еду?»\n"
        "📋 Синхронизация с Google Sheets\n"
        "✏️ Редактирование и удаление операций\n"
        "📦 Пакетный ввод нескольких операций\n\n"
        "🔹 *Тарифы:*\n"
        "💎 Premium (190₽/мес) — весь функционал\n"
        "💎 Premium (1900₽/год) — выгода ~17%\n"
        "🎁 Пробный период — 14 дней бесплатно!\n\n"
        "🔹 *Технологии:*\n"
        "Python • python-telegram-bot • OpenAI GPT\n"
        "SQLAlchemy • YooKassa • Google Sheets API\n\n"
        "Версия: 3.0"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Попробовать Premium", callback_data=MENU_PREMIUM)],
        [
            InlineKeyboardButton("❓ Помощь", callback_data=MENU_HELP),
            InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN),
        ],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


def _build_accounts_screen(accounts: list) -> tuple[str, InlineKeyboardMarkup]:
    """Build the unified accounts screen text + inline keyboard.

    Each account row gets its own action row: [⭐ if not default] [✏️ Rename] [🗑 Delete].
    """
    if not accounts:
        text = (
            "💰 *Мои счета*\n\n"
            "Здесь пусто. Чтобы создать первый счёт — нажми кнопку ниже."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать счёт", callback_data="acct:custom")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
        ])
        return text, keyboard

    lines = ["💰 *Мои счета*\n"]
    rows: list[list[InlineKeyboardButton]] = []
    for acc in accounts:
        mark = "⭐" if acc.is_default else "•"
        lines.append(f"{mark} {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}")
        action_row = []
        if not acc.is_default:
            action_row.append(InlineKeyboardButton("⭐", callback_data=f"acct:setmain:{acc.id}"))
        action_row.append(InlineKeyboardButton("✏️", callback_data=f"acct:rename:{acc.id}"))
        action_row.append(InlineKeyboardButton("🗑", callback_data=f"acct:delete:{acc.id}"))
        rows.append(action_row)

    lines.append("")
    lines.append("⭐ — главный счёт (по умолчанию). Нажми ⭐ у другого, чтобы переключить.")

    rows.append([InlineKeyboardButton("➕ Создать счёт", callback_data="acct:custom")])
    rows.append([
        InlineKeyboardButton("📄 Google Sheets", callback_data="cmd:sheets"),
        InlineKeyboardButton("📜 История", callback_data="cmd:history"),
    ])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _show_accounts_screen(update: Update, *, db=None, user=None, edit_message: bool = True):
    """Render the unified accounts screen.

    Works both from callback (edit_message=True) and from a plain message (edit_message=False).
    Accepts pre-resolved db/user to avoid opening a new session inside another one.
    """
    own_db = False
    if db is None:
        db = SessionLocal()
        own_db = True

    try:
        if user is None:
            tg_user_id = update.effective_user.id
            user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
            if not user:
                msg = "Сначала начни работу: /start"
                if edit_message and update.callback_query:
                    await update.callback_query.edit_message_text(msg)
                else:
                    await update.effective_message.reply_text(msg)
                return

        accounts = db.query(Account).filter(Account.user_id == user.id).order_by(Account.id.asc()).all()
        text, keyboard = _build_accounts_screen(accounts)

        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error("Error in _show_accounts_screen: %s", e, exc_info=True)
        err = "❌ Ошибка при загрузке счетов."
        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text(err)
        else:
            await update.effective_message.reply_text(err)
    finally:
        if own_db:
            db.close()


async def _show_help_full(update: Update):
    """Help entry from callback — same sectioned interface as /help."""
    from bot.handlers import HELP_INTRO, _help_sections_keyboard

    query = update.callback_query
    await query.edit_message_text(
        HELP_INTRO,
        reply_markup=_help_sections_keyboard(),
        parse_mode="Markdown",
    )


async def _show_help_section(update: Update, section: str):
    """Show one specific help section."""
    from bot.handlers import HELP_SECTIONS

    query = update.callback_query
    text = HELP_SECTIONS.get(section)
    if not text:
        text = "Раздел не найден."

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад к разделам", callback_data=MENU_HELP)],
        [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _handle_setmain_callback(update: Update, account_id: int):
    """⭐ button next to an account — set it as the default."""
    from services.ledger import set_default_account

    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Ошибка.")
            return
        account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
        if not account:
            await query.answer("Счёт не найден.", show_alert=True)
            return
        set_default_account(db, user.id, account.id)
        await query.answer(f"⭐ Главный: {account.name}")
        # Re-render the same screen with updated state
        await _show_accounts_screen(update, db=db, user=user, edit_message=True)
    finally:
        db.close()


async def _handle_rename_start(update: Update, context, account_id: int):
    """✏️ button — enter rename FSM."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.answer("Ошибка.", show_alert=True)
            return
        account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
        if not account:
            await query.answer("Счёт не найден.", show_alert=True)
            return

        context.user_data["rename_account_id"] = account.id
        await query.edit_message_text(
            f"✏️ *Переименовать «{account.name}»*\n\n"
            "Напиши новое название сообщением (1–50 символов).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="acct:rename_cancel")],
            ]),
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def handle_rename_account_input(update: Update, context):
    """Free-text new name for an account being renamed."""
    from services.ledger import rename_account, find_account_by_name

    account_id = context.user_data.get("rename_account_id")
    if not account_id:
        return

    new_name = (update.message.text or "").strip()
    if not new_name or len(new_name) > 50:
        await update.message.reply_text("❌ Название должно быть 1–50 символов. Попробуй ещё раз.")
        return

    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            context.user_data.pop("rename_account_id", None)
            return

        clash = find_account_by_name(db, user.id, new_name, exact_only=True)
        if clash and clash.id != account_id:
            await update.message.reply_text(
                f"❌ Счёт «{new_name}» уже существует. Выбери другое имя.",
            )
            return

        try:
            rename_account(db, user.id, account_id, new_name)
        except ValueError:
            await update.message.reply_text("❌ Счёт не найден.")
            context.user_data.pop("rename_account_id", None)
            return

        context.user_data.pop("rename_account_id", None)
        await update.message.reply_text(f"✅ Новое имя: «{new_name}».")
        await _show_accounts_screen(update, db=db, user=user, edit_message=False)
    finally:
        db.close()


async def _handle_rename_cancel(update: Update, context):
    """Cancel rename flow."""
    query = update.callback_query
    context.user_data.pop("rename_account_id", None)
    await query.answer("Отменено.")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == update.effective_user.id).first()
        await _show_accounts_screen(update, db=db, user=user, edit_message=True)
    finally:
        db.close()


async def _handle_delete_prompt(update: Update, account_id: int):
    """🗑 button — show delete confirmation with operation count."""
    from services.ledger import count_account_transactions

    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.answer("Ошибка.", show_alert=True)
            return
        account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
        if not account:
            await query.answer("Счёт не найден.", show_alert=True)
            return

        tx_count = count_account_transactions(db, user.id, account.id)

        text_lines = [
            f"🗑 *Удалить счёт «{account.name}»?*",
            "",
            f"Валюта: {account.currency}",
            f"Баланс: {format_amount(account.balance, account.currency)}",
        ]
        if tx_count > 0:
            text_lines.append(f"Вместе со счётом удалится {tx_count} операций.")
        text_lines.append("")
        text_lines.append("Это необратимо.")

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Удалить", callback_data=f"acct:delete_confirm:{account.id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="cmd:accounts"),
            ],
        ])
        await query.edit_message_text("\n".join(text_lines), reply_markup=keyboard, parse_mode="Markdown")
    finally:
        db.close()


async def _handle_delete_confirm(update: Update, account_id: int):
    """✅ Удалить — actually delete the account (cascade)."""
    from services.ledger import delete_account

    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.answer("Ошибка.", show_alert=True)
            return
        account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
        if not account:
            await query.answer("Счёт не найден.", show_alert=True)
            return

        name = account.name
        try:
            delete_account(db, user.id, account.id)
        except Exception as e:
            logger.error("delete_account failed: %s", e, exc_info=True)
            await query.edit_message_text(f"❌ Не удалось удалить: {e}")
            return

        await query.answer(f"🗑 «{name}» удалён.")
        # Re-fetch user to get refreshed default_account_id
        user = db.query(User).filter(User.id == user.id).first()
        await _show_accounts_screen(update, db=db, user=user, edit_message=True)
    finally:
        db.close()


async def account_management_callback(update: Update, context):
    """Dispatch acct:setmain / acct:rename / acct:delete / acct:delete_confirm callbacks."""
    query = update.callback_query
    data = query.data or ""

    if data == "acct:rename_cancel":
        await _handle_rename_cancel(update, context)
        return

    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return

    action = parts[1]
    try:
        account_id = int(parts[2])
    except (ValueError, IndexError):
        await query.answer("Некорректный счёт.", show_alert=True)
        return

    if action == "setmain":
        await query.answer()
        await _handle_setmain_callback(update, account_id)
    elif action == "rename":
        await query.answer()
        await _handle_rename_start(update, context, account_id)
    elif action == "delete":
        await query.answer()
        await _handle_delete_prompt(update, account_id)
    elif action == "delete_confirm":
        await _handle_delete_confirm(update, account_id)
    else:
        await query.answer()


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu navigation callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # Subscription check for protected actions (not navigation/info screens)
    _EXEMPT = {MENU_MAIN, MENU_PREMIUM, MENU_STATUS, MENU_HELP, MENU_ABOUT, "cmd:help"}
    if data not in _EXEMPT:
        from bot.middleware import check_subscription
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.tg_user_id == update.effective_user.id).first()
            if user:
                allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
                if not allowed:
                    await query.edit_message_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
                    return
        finally:
            db.close()

    if data == MENU_MAIN:
        await _show_main_menu(update, edit_message=True)
    elif data == MENU_FINANCES:
        await _show_finances_menu(update)
    elif data == MENU_REPORTS:
        await _show_reports_menu(update)
    elif data == MENU_PREMIUM:
        from bot.subscription import _show_premium_menu
        await _show_premium_menu(update, edit_message=True)
    elif data == MENU_STATUS:
        from bot.subscription import _show_status
        await _show_status(update, edit_message=True)
    elif data == MENU_HELP:
        await _show_help_menu(update)
    elif data == MENU_SETTINGS:
        await _show_settings_menu(update)
    elif data == MENU_ABOUT:
        await _show_about(update)
    elif data == "cmd:accounts":
        await _show_accounts_screen(update, edit_message=True)
    elif data == "cmd:history":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])
        await query.edit_message_text(
            "📜 Напиши «история» или «покажи расходы за месяц».",
            reply_markup=keyboard,
        )
    elif data == "cmd:analytics":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])
        await query.edit_message_text(
            "🔍 Напиши «почему много на еду» или похожий вопрос.",
            reply_markup=keyboard,
        )
    elif data == "cmd:help":
        await _show_help_full(update)
    elif data == "cmd:sheets":
        await _show_sheets_screen(update)
    elif data == "cmd:sheets:export":
        await _handle_sheets_export_callback(update, context)
    elif data == "cmd:sheets:import":
        await _handle_sheets_import_callback(update, context)
    elif data == "cmd:sheets:reset":
        await _handle_sheets_reset_callback(update)
    elif data.startswith("cmd:help:"):
        await _show_help_section(update, data.replace("cmd:help:", ""))
    elif data == "settings:timezone":
        await _show_timezone_picker(update)
    elif data.startswith("settings:tz:"):
        await _handle_timezone_set(update, data)
    elif data.startswith("report:"):
        await _handle_report_shortcut(update, context, data)


async def _show_sheets_screen(update: Update):
    """Full Google Sheets screen — status + Export/Import/Disconnect or setup instructions."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начни работу: /start")
            return

        try:
            from services.google_sheets_client import is_configured, get_service_account_email
        except Exception as e:
            logger.warning("google_sheets_client import failed: %s", e)
            is_configured = lambda: False  # noqa: E731
            get_service_account_email = lambda: None  # noqa: E731

        if not is_configured():
            await query.edit_message_text(
                "❌ Интеграция Google Sheets не настроена на стороне бота.\n"
                "Попроси администратора включить её.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
                ]),
            )
            return

        if user.google_sheets_spreadsheet_id:
            text = (
                "📄 *Google Sheets*\n\n"
                f"Подключена таблица:\n`{user.google_sheets_spreadsheet_id}`\n\n"
                "Выгрузка перезаписывает таблицу данными из бота.\n"
                "Загрузка заменяет данные в боте данными из таблицы."
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📤 Экспорт", callback_data="cmd:sheets:export"),
                    InlineKeyboardButton("📥 Импорт", callback_data="cmd:sheets:import"),
                ],
                [InlineKeyboardButton("🔌 Отключить таблицу", callback_data="cmd:sheets:reset")],
                [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
            ])
        else:
            sa_email = get_service_account_email() or "rich-uncle-scrooge-bot-648@rich-uncle-scrooge.iam.gserviceaccount.com"
            text = (
                "📄 *Подключение Google Sheets*\n\n"
                "Чтобы синхронизировать данные с таблицей:\n\n"
                "1. Создай таблицу в Google Sheets.\n"
                "2. Нажми *Share* → добавь *Editor* для:\n"
                f"`{sa_email}`\n"
                "3. Пришли мне команду:\n"
                "`/sheets <ссылка>`\n\n"
                "После этого вернись на этот экран — появятся кнопки экспорта и импорта."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
            ])

        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    finally:
        db.close()


async def _handle_sheets_export_callback(update: Update, context):
    """Trigger /sheets_export via the inline button."""
    query = update.callback_query
    await query.answer("⏳ Экспортирую…")
    from bot.sheets import sheets_export_command
    # The export command sends its own messages via update.message.reply_text,
    # but here we have a callback. We re-use the command by simulating: it relies
    # on update.message; we pass update.callback_query.message so reply_text works.
    class _FakeUpdate:
        def __init__(self, real):
            self._real = real
        @property
        def effective_user(self):
            return self._real.effective_user
        @property
        def message(self):
            return self._real.callback_query.message
        def __getattr__(self, item):
            return getattr(self._real, item)
    await sheets_export_command(_FakeUpdate(update), context)


async def _handle_sheets_import_callback(update: Update, context):
    """Trigger /sheets_import via the inline button."""
    query = update.callback_query
    await query.answer("⏳ Загружаю предпросмотр…")
    from bot.sheets import sheets_import_command
    class _FakeUpdate:
        def __init__(self, real):
            self._real = real
        @property
        def effective_user(self):
            return self._real.effective_user
        @property
        def message(self):
            return self._real.callback_query.message
        def __getattr__(self, item):
            return getattr(self._real, item)
    await sheets_import_command(_FakeUpdate(update), context)


async def _handle_sheets_reset_callback(update: Update):
    """Disconnect the Google Sheets binding."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.answer("Ошибка.", show_alert=True)
            return
        user.google_sheets_spreadsheet_id = None
        db.commit()
        await query.answer("🔌 Отключено.")
        await _show_sheets_screen(update)
    finally:
        db.close()


async def _show_timezone_picker(update: Update):
    """Show timezone selection buttons."""
    query = update.callback_query
    common_timezones = [
        ("🇷🇺 Москва (UTC+3)", "Europe/Moscow"),
        ("🇷🇺 Калининград (UTC+2)", "Europe/Kaliningrad"),
        ("🇷🇺 Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
        ("🇷🇺 Новосибирск (UTC+7)", "Asia/Novosibirsk"),
        ("🇷🇺 Владивосток (UTC+10)", "Asia/Vladivostok"),
        ("🇬🇧 Лондон (UTC+0/+1)", "Europe/London"),
        ("🇪🇺 Берлин (UTC+1/+2)", "Europe/Berlin"),
        ("🇺🇸 Нью-Йорк (UTC-5/-4)", "America/New_York"),
        ("🇹🇭 Бангкок (UTC+7)", "Asia/Bangkok"),
        ("🇦🇪 Дубай (UTC+4)", "Asia/Dubai"),
    ]
    buttons = []
    for label, tz in common_timezones:
        buttons.append([InlineKeyboardButton(label, callback_data=f"settings:tz:{tz}")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data=MENU_SETTINGS)])

    await query.edit_message_text(
        "🕐 *Выберите таймзону:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


async def _handle_timezone_set(update: Update, data: str):
    """Set user timezone."""
    query = update.callback_query
    tz = data.replace("settings:tz:", "")
    tg_user_id = update.effective_user.id

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if user:
            user.timezone = tz
            db.commit()

        await query.edit_message_text(
            f"✅ Таймзона установлена: *{tz}*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Настройки", callback_data=MENU_SETTINGS)],
                [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
            ]),
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def _handle_report_shortcut(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Handle quick report buttons from menu."""
    from bot.middleware import check_subscription
    from services.reports import get_report, format_report_text
    from db.models import User

    query = update.callback_query
    period = data.replace("report:", "")
    tg_user_id = update.effective_user.id

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начните работу: /start")
            return

        # Check subscription before generating report
        allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
        if not allowed:
            await query.edit_message_text(
                paywall_text,
                reply_markup=paywall_keyboard,
                parse_mode="Markdown",
            )
            return

        report = get_report(db, user.id, period_preset=period, user_timezone=user.timezone)
        text = format_report_text(report, user.timezone)

        keyboard_buttons = [[InlineKeyboardButton(
            "🤖 Анализ от GPT",
            callback_data=f"fin:report_analysis:{user.tg_user_id}:{period}"
        )]]
        keyboard_buttons.append([InlineKeyboardButton("↩️ К отчётам", callback_data=MENU_REPORTS)])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard_buttons),
        )
    except Exception as e:
        logger.error("Report shortcut error: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при формировании отчёта.")
    finally:
        db.close()
