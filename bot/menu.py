"""Menu handlers — main menu, reports menu, settings, navigation."""
import logging
from datetime import datetime

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
            accounts = db.query(Account).filter(Account.user_id == user.id).all()
            if not accounts:
                msg = "💰 *Мои счета*\n\nУ вас пока нет счетов.\nНапишите: «создай счет наличка rub»"
            else:
                lines = ["💰 *Мои счета*\n"]
                for acc in accounts:
                    default_mark = " ⭐" if acc.is_default else ""
                    lines.append(f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}{default_mark}")
                msg = "\n".join(lines)
            buttons = [
                [
                    InlineKeyboardButton("➕ Создать счёт", callback_data="acct:custom"),
                    InlineKeyboardButton("⭐ Назначить главный", callback_data="cmd:set_default"),
                ],
                [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
            ]
            if not accounts:
                # No accounts — only show create button
                buttons = [
                    [InlineKeyboardButton("➕ Создать счёт", callback_data="acct:custom")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
                ]
            keyboard = InlineKeyboardMarkup(buttons)
            await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

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
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Инструкция", callback_data="cmd:help")],
                [InlineKeyboardButton("ℹ️ О боте", callback_data=MENU_ABOUT)],
                [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
            ])
            await update.message.reply_text(
                "❓ *Помощь*\n\n"
                "💬 Просто пишите как обычно — бот всё поймёт:\n"
                "• «кофе 320» — расход\n"
                "• «+50000 зп» — доход\n"
                "• «переведи 10к с карты на нал» — перевод\n"
                "• «бюджет на кофе 3000» — установить бюджет\n"
                "• «отчет за месяц» — отчёт\n"
                "• «почему много на еду» — аналитика",
                reply_markup=keyboard,
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
                InlineKeyboardButton("📊 Мои финансы", callback_data=MENU_FINANCES),
                InlineKeyboardButton("📈 Отчёты", callback_data=MENU_REPORTS),
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
    """Show user's accounts and quick actions."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начните работу: /start")
            return

        accounts = db.query(Account).filter(Account.user_id == user.id).all()

        if not accounts:
            text = (
                "📊 *Мои финансы*\n\n"
                "У вас пока нет счетов.\n\n"
                "Создайте первый счёт:\n"
                "Напишите: «создай счет наличка rub»"
            )
        else:
            lines = ["📊 *Мои финансы*\n"]
            for acc in accounts:
                default_mark = " ⭐" if acc.is_default else ""
                lines.append(
                    f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}{default_mark}"
                )
            text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💰 Счета", callback_data="cmd:accounts"),
                InlineKeyboardButton("📜 История", callback_data="cmd:history"),
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
    """Show abbreviated help in menu."""
    query = update.callback_query
    text = (
        "❓ *Помощь*\n\n"
        "💬 Просто пишите как обычно — бот всё поймёт:\n\n"
        "• «кофе 320» — расход\n"
        "• «+50000 зп» — доход\n"
        "• «переведи 10к с карты на нал» — перевод\n"
        "• «отчет за месяц» — отчёт\n"
        "• «почему много на еду» — аналитика\n\n"
        "📋 Подробная справка: /help\n"
        "📊 Счета: /accounts\n"
        "📈 Отчёт: /report"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Полная справка", callback_data="cmd:help")],
        [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


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


async def _show_accounts_inline(update: Update):
    """Show accounts list via callback query (edit message instead of reply)."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начните работу: /start")
            return

        accounts = db.query(Account).filter(Account.user_id == user.id).all()

        if not accounts:
            text = (
                "💰 *Мои счета*\n\n"
                "У вас пока нет счетов.\n\n"
                "Создайте первый счёт:\n"
                "Напишите: «создай счет наличка rub»"
            )
        else:
            lines = ["💰 *Мои счета*\n"]
            for acc in accounts:
                default_mark = " ⭐" if acc.is_default else ""
                lines.append(
                    f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}{default_mark}"
                )
            text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error("Error in _show_accounts_inline: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке счетов.")
    finally:
        db.close()


async def _show_help_full(update: Update):
    """Show full help text via callback query (edit message)."""
    query = update.callback_query
    # Telegram messages have a 4096 char limit; help text is long, so we truncate
    # to the most essential parts for inline display
    text = (
        "💰 *Дядя Скрудж — справка*\n\n"
        "Пишешь как обычно — бот сам понимает.\n\n"
        "*💸 Расходы:*\n"
        "• кофе 320\n"
        "• такси 500\n"
        "• продукты 1500\n\n"
        "*💰 Доходы:*\n"
        "• +50000 зп\n"
        "• получил 10000 возврат\n\n"
        "*🔄 Переводы:*\n"
        "• переведи 10к с карты на нал\n\n"
        "*💳 Счета:*\n"
        "• создай счет наличка rub\n"
        "• удали счет юмани\n"
        "• главный счет карта\n\n"
        "*📊 Отчёты:*\n"
        "• отчет за ноябрь\n"
        "• статистика за неделю\n\n"
        "*🔍 Аналитика:*\n"
        "• почему так много на еду\n\n"
        "*📦 Несколько операций:*\n"
        "• кофе 300, такси 500, обед 400\n\n"
        "*✏️ Редактирование:*\n"
        "• измени 3 сумма 500\n"
        "• удали 5\n\n"
        "*📋 Бюджеты:*\n"
        "• бюджет на кофе 3000\n"
        "• лимит на еду 15000₽\n\n"
        "🔥 Стрики и ачивки за ежедневные записи\n"
        "🎤 Голосовые сообщения тоже работают!\n"
        "📄 Google Sheets: /sheets\n\n"
        "Полная справка: /help"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_set_default_picker(update: Update):
    """Show account list for selecting default account."""
    query = update.callback_query
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начните работу: /start")
            return
        accounts = db.query(Account).filter(Account.user_id == user.id).all()
        if not accounts:
            await query.edit_message_text(
                "У вас пока нет счетов.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Назад", callback_data=MENU_MAIN)],
                ]),
            )
            return
        buttons = []
        for acc in accounts:
            mark = "⭐ " if acc.is_default else ""
            buttons.append([InlineKeyboardButton(
                f"{mark}{acc.name} ({acc.currency})",
                callback_data=f"cmd:setdef:{acc.id}",
            )])
        buttons.append([InlineKeyboardButton("↩️ Назад", callback_data=MENU_MAIN)])
        await query.edit_message_text(
            "⭐ *Выберите главный счёт:*\n\n"
            "Главный счёт используется по умолчанию для расходов и доходов.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def _handle_set_default(update: Update, data: str):
    """Set a specific account as default."""
    from services.ledger import set_default_account
    query = update.callback_query
    account_id = int(data.replace("cmd:setdef:", ""))
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Ошибка.")
            return
        account = db.query(Account).filter(Account.id == account_id, Account.user_id == user.id).first()
        if not account:
            await query.edit_message_text("Счёт не найден.")
            return
        set_default_account(db, user.id, account.name)
        await query.edit_message_text(
            f"⭐ Счёт «{account.name}» назначен главным!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Счета", callback_data="cmd:accounts")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data=MENU_MAIN)],
            ]),
        )
    finally:
        db.close()


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
        await _show_accounts_inline(update)
    elif data == "cmd:history":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])
        await query.edit_message_text(
            "📜 Напишите «история» или «покажи расходы за месяц»",
            reply_markup=keyboard,
        )
    elif data == "cmd:analytics":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])
        await query.edit_message_text(
            "🔍 Напишите «почему много на еду» или подобный вопрос",
            reply_markup=keyboard,
        )
    elif data == "cmd:help":
        await _show_help_full(update)
    elif data == "cmd:set_default":
        await _show_set_default_picker(update)
    elif data.startswith("cmd:setdef:"):
        await _handle_set_default(update, data)
    elif data == "cmd:sheets":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Главное меню", callback_data=MENU_MAIN)],
        ])
        await query.edit_message_text(
            "📊 Используйте /sheets для настройки Google Sheets",
            reply_markup=keyboard,
        )
    elif data == "settings:timezone":
        await _show_timezone_picker(update)
    elif data.startswith("settings:tz:"):
        await _handle_timezone_set(update, data)
    elif data.startswith("report:"):
        await _handle_report_shortcut(update, context, data)


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
