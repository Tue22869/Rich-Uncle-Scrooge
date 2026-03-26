"""Account setup handlers — button-based account creation for new users."""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.models import User, Account
from db.session import SessionLocal
from services.ledger import get_or_create_user, create_account
from utils.money import format_amount

logger = logging.getLogger(__name__)

# Callback data prefixes
ACCT_CREATE_PREFIX = "acct:create:"
ACCT_CUSTOM = "acct:custom"
ACCT_CUSTOM_CURRENCY = "acct:currency:"


async def account_setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle account creation callbacks from /start onboarding."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith(ACCT_CREATE_PREFIX):
        await _handle_quick_create(update, context, data)
    elif data == ACCT_CUSTOM:
        await _handle_custom_prompt(update, context)
    elif data.startswith(ACCT_CUSTOM_CURRENCY):
        await _handle_custom_currency(update, context, data)


async def _handle_quick_create(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Create a predefined account (Наличные RUB, Карта RUB, etc.)."""
    query = update.callback_query
    tg_user_id = update.effective_user.id

    # Parse: acct:create:Name:CURRENCY
    parts = data.replace(ACCT_CREATE_PREFIX, "").split(":")
    if len(parts) != 2:
        await query.edit_message_text("Ошибка: неверный формат.")
        return

    name, currency = parts[0], parts[1].upper()

    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)

        # Check if account with this name already exists
        existing = (
            db.query(Account)
            .filter(Account.user_id == user.id, Account.name == name)
            .first()
        )
        if existing:
            await query.edit_message_text(
                f"Счёт «{name}» уже существует!\n\n"
                "Используй кнопки ниже для других действий.",
            )
            return

        account = create_account(db, user.id, name, currency)

        # Check how many accounts the user now has
        total = db.query(Account).filter(Account.user_id == user.id).count()
        accounts = db.query(Account).filter(Account.user_id == user.id).all()

        accounts_text = "\n".join([
            f"  {'⭐ ' if acc.is_default else '• '}{acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}"
            for acc in accounts
        ])

        buttons = []
        if total < 5:
            buttons.append([
                InlineKeyboardButton("➕ Добавить ещё счёт", callback_data="acct:more"),
            ])
        buttons.extend([
            [
                InlineKeyboardButton("📊 Мои финансы", callback_data="menu:finances"),
                InlineKeyboardButton("💎 Premium", callback_data="menu:premium"),
            ],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
        ])

        await query.edit_message_text(
            f"✅ Счёт «{name}» ({currency}) создан!\n\n"
            f"{'⭐ Это ваш основной счёт.' if account.is_default else ''}\n\n"
            f"💰 Ваши счета:\n{accounts_text}\n\n"
            "Теперь просто пишите о расходах и доходах:\n"
            "• «кофе 320» — расход\n"
            "• «+50000 зп» — доход\n"
            "• «отчет за месяц» — отчёт",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        # Activate persistent bottom keyboard via a lightweight message
        from bot.menu import PERSISTENT_MENU
        await query.message.reply_text("⌨️", reply_markup=PERSISTENT_MENU)
    except Exception as e:
        logger.error("Account quick create error: %s", e, exc_info=True)
        await query.edit_message_text("Ошибка при создании счёта. Попробуйте позже.")
    finally:
        db.close()


async def _handle_custom_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to choose currency for custom account."""
    query = update.callback_query

    # Clear any leftover state from previous attempt
    context.user_data.pop("custom_account_currency", None)
    context.user_data.pop("custom_account_name", None)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇷🇺 RUB", callback_data="acct:currency:RUB"),
            InlineKeyboardButton("🇺🇸 USD", callback_data="acct:currency:USD"),
        ],
        [
            InlineKeyboardButton("🇪🇺 EUR", callback_data="acct:currency:EUR"),
            InlineKeyboardButton("₮ USDT", callback_data="acct:currency:USDT"),
        ],
        [InlineKeyboardButton("↩️ Назад", callback_data="acct:back_to_start")],
    ])

    await query.edit_message_text(
        "✏️ *Создание своего счёта*\n\n"
        "Шаг 1: Выберите валюту:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def _handle_custom_currency(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Handle currency selection for custom account — prompt for name."""
    query = update.callback_query
    currency = data.replace(ACCT_CUSTOM_CURRENCY, "").upper()

    if currency not in ("RUB", "USD", "EUR", "USDT"):
        await query.edit_message_text("❌ Неизвестная валюта.")
        return

    # Store currency in user_data for the next step
    context.user_data["custom_account_currency"] = currency

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Назад", callback_data="acct:custom")],
    ])

    await query.edit_message_text(
        f"✏️ *Создание своего счёта ({currency})*\n\n"
        "Шаг 2: Напишите название счёта в чат.\n\n"
        "Примеры:\n"
        "• Тинькофф\n"
        "• Крипта\n"
        "• Наличные доллары",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def _handle_add_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show buttons to add more accounts after first creation."""
    query = update.callback_query
    await query.answer()

    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tg_user_id == tg_user_id).first()
        if not user:
            await query.edit_message_text("Сначала начните работу: /start")
            return

        existing = db.query(Account).filter(Account.user_id == user.id).all()
        existing_names = {acc.name for acc in existing}

        buttons = []
        # Offer common accounts that don't exist yet
        suggestions = [
            ("💵 Наличные (RUB)", "Наличные", "RUB"),
            ("💳 Карта (RUB)", "Карта", "RUB"),
            ("💲 Карта (USD)", "Карта USD", "USD"),
            ("💶 Карта (EUR)", "Карта EUR", "EUR"),
        ]
        row = []
        for label, name, cur in suggestions:
            if name not in existing_names:
                row.append(InlineKeyboardButton(label, callback_data=f"acct:create:{name}:{cur}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton("✏️ Создать свой счёт", callback_data="acct:custom")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")])

        await query.edit_message_text(
            "➕ *Добавить ещё счёт*\n\n"
            "Выберите или создайте свой:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def _handle_back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to the /start onboarding screen."""
    query = update.callback_query
    await query.answer()

    # Clear custom account creation state
    context.user_data.pop("custom_account_currency", None)
    context.user_data.pop("custom_account_name", None)

    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        accounts = db.query(Account).filter(Account.user_id == user.id).all()

        if not accounts:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💵 Наличные (RUB)", callback_data="acct:create:Наличные:RUB"),
                    InlineKeyboardButton("💳 Карта (RUB)", callback_data="acct:create:Карта:RUB"),
                ],
                [
                    InlineKeyboardButton("💲 Карта (USD)", callback_data="acct:create:Карта:USD"),
                    InlineKeyboardButton("💶 Карта (EUR)", callback_data="acct:create:Карта:EUR"),
                ],
                [
                    InlineKeyboardButton("✏️ Создать свой счёт", callback_data="acct:custom"),
                ],
                [
                    InlineKeyboardButton("❓ Помощь", callback_data="menu:help"),
                ],
            ])
            await query.edit_message_text(
                "💰 *Дядя Скрудж к вашим услугам!*\n\n"
                "Буду считать твои деньги и следить, чтобы ни одна монетка не пропала. 🦆\n\n"
                "Для начала создай свой первый счёт:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
        else:
            # User already has accounts — show main menu
            from bot.menu import _show_main_menu
            await _show_main_menu(update, edit_message=True)
    finally:
        db.close()


async def handle_custom_account_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input during custom account creation (name or balance step)."""
    # Step 3: initial balance input
    if context.user_data.get("custom_account_name"):
        await _handle_custom_balance_input(update, context)
        return

    # Step 2: name input
    currency = context.user_data.get("custom_account_currency")
    if not currency:
        return

    name = update.message.text.strip()
    if not name or len(name) > 50:
        await update.message.reply_text(
            "❌ Название счёта должно быть от 1 до 50 символов. Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Назад", callback_data="acct:custom")],
            ]),
        )
        return

    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        existing = (
            db.query(Account)
            .filter(Account.user_id == user.id, Account.name == name)
            .first()
        )
        if existing:
            await update.message.reply_text(
                f"❌ Счёт «{name}» уже существует! Выберите другое название.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Назад", callback_data="acct:custom")],
                ]),
            )
            return
    finally:
        db.close()

    # Store name, move to balance step
    context.user_data["custom_account_name"] = name

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Пропустить (баланс 0)", callback_data="acct:balance:0")],
        [InlineKeyboardButton("↩️ Назад", callback_data="acct:custom")],
    ])
    await update.message.reply_text(
        f"✏️ *Создание счёта «{name}» ({currency})*\n\n"
        "Шаг 3: Введите начальный баланс (число).\n"
        "Или нажмите «Пропустить» для баланса 0.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def _handle_custom_balance_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle initial balance text input for custom account creation."""
    from decimal import Decimal, InvalidOperation

    currency = context.user_data.pop("custom_account_currency", None)
    name = context.user_data.pop("custom_account_name", None)
    if not currency or not name:
        return

    text = update.message.text.strip().replace(",", ".").replace(" ", "")
    try:
        balance = Decimal(text)
        if balance < 0:
            raise InvalidOperation("negative")
    except (InvalidOperation, ValueError):
        # Re-set state so user can try again
        context.user_data["custom_account_currency"] = currency
        context.user_data["custom_account_name"] = name
        await update.message.reply_text(
            "❌ Введите число (например: 50000 или 1500.50).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить (баланс 0)", callback_data="acct:balance:0")],
            ]),
        )
        return

    await _finalize_custom_account(update, name, currency, balance)


async def _handle_balance_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle balance skip button (acct:balance:0)."""
    from decimal import Decimal
    query = update.callback_query
    await query.answer()

    currency = context.user_data.pop("custom_account_currency", None)
    name = context.user_data.pop("custom_account_name", None)
    if not currency or not name:
        await query.edit_message_text("Сессия создания счёта истекла. Попробуйте снова.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Создать счёт", callback_data="acct:custom")],
            ]))
        return

    await _finalize_custom_account_from_callback(query, name, currency, Decimal("0"))


async def _finalize_custom_account(update: Update, name: str, currency: str, balance):
    """Create the custom account and show result (from message context)."""
    from decimal import Decimal
    tg_user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        account = create_account(db, user.id, name, currency, balance)

        accounts = db.query(Account).filter(Account.user_id == user.id).all()
        accounts_text = "\n".join([
            f"  {'⭐ ' if acc.is_default else '• '}{acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}"
            for acc in accounts
        ])

        buttons = []
        if len(accounts) < 5:
            buttons.append([InlineKeyboardButton("➕ Добавить ещё счёт", callback_data="acct:more")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")])

        bal_text = f" с балансом {format_amount(balance, currency)}" if balance > 0 else ""
        await update.message.reply_text(
            f"✅ Счёт «{name}» ({currency}) создан{bal_text}!\n\n"
            f"{'⭐ Это ваш основной счёт.\n' if account.is_default else ''}"
            f"💰 Ваши счета:\n{accounts_text}\n\n"
            "Теперь просто пишите о расходах и доходах!",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        # Activate persistent bottom keyboard via a lightweight message
        from bot.menu import PERSISTENT_MENU
        await update.message.reply_text("⌨️", reply_markup=PERSISTENT_MENU)
    except Exception as e:
        logger.error("Custom account creation error: %s", e, exc_info=True)
        await update.message.reply_text("❌ Ошибка при создании счёта. Попробуйте позже.")
    finally:
        db.close()


async def _finalize_custom_account_from_callback(query, name: str, currency: str, balance):
    """Create the custom account and show result (from callback context)."""
    from decimal import Decimal
    tg_user_id = query.from_user.id
    db = SessionLocal()
    try:
        user = get_or_create_user(db, tg_user_id)
        account = create_account(db, user.id, name, currency, balance)

        accounts = db.query(Account).filter(Account.user_id == user.id).all()
        accounts_text = "\n".join([
            f"  {'⭐ ' if acc.is_default else '• '}{acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}"
            for acc in accounts
        ])

        buttons = []
        if len(accounts) < 5:
            buttons.append([InlineKeyboardButton("➕ Добавить ещё счёт", callback_data="acct:more")])
        buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")])

        await query.edit_message_text(
            f"✅ Счёт «{name}» ({currency}) создан!\n\n"
            f"{'⭐ Это ваш основной счёт.\n' if account.is_default else ''}"
            f"💰 Ваши счета:\n{accounts_text}\n\n"
            "Теперь просто пишите о расходах и доходах!",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        # Activate persistent bottom keyboard via a lightweight message
        from bot.menu import PERSISTENT_MENU
        await query.message.reply_text("⌨️", reply_markup=PERSISTENT_MENU)
    except Exception as e:
        logger.error("Custom account creation error: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при создании счёта.")
    finally:
        db.close()


async def account_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle secondary account action callbacks (more, back_to_start)."""
    query = update.callback_query
    data = query.data

    if data == "acct:more":
        await _handle_add_more(update, context)
    elif data == "acct:back_to_start":
        await _handle_back_to_start(update, context)
