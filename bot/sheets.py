"""Google Sheets command handlers: /sheets, /sheets_export, /sheets_import."""
import asyncio
import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.models import PendingAction, ActionType, PendingStatus, Account, Transaction
from services.ledger import get_or_create_user

from bot.helpers import get_db

logger = logging.getLogger(__name__)


async def sheets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sheets command: configure Google Sheets connection.

    Usage:
    - /sheets -> show instructions or current status
    - /sheets <spreadsheet_url_or_id> -> save user's spreadsheet id
    - /sheets reset -> remove saved spreadsheet id
    """
    logger.info(f"sheets_command called by user {update.effective_user.id}, args: {context.args if context else None}")
    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)

        from services.google_sheets_client import (
            is_configured,
            get_service_account_email,
        )

        if not is_configured():
            await update.message.reply_text(
                "❌ Google Sheets интеграция на стороне бота не настроена.\n"
                "Попроси администратора настроить авторизацию (service account или OAuth)."
            )
            return

        args = (context.args or []) if context else []
        if args:
            raw = " ".join(args).strip()
            if raw.lower() in {"reset", "off", "disable", "удалить", "сброс"}:
                user.google_sheets_spreadsheet_id = None
                db.commit()
                await update.message.reply_text("✅ Готово. Привязка Google Sheets удалена.")
                return

            import re

            m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", raw)
            spreadsheet_id = m.group(1) if m else raw
            spreadsheet_id = spreadsheet_id.strip()

            if not re.fullmatch(r"[a-zA-Z0-9-_]{20,}", spreadsheet_id):
                await update.message.reply_text(
                    "❌ Не похоже на Spreadsheet ID.\n\n"
                    "Пришли ссылку вида:\n"
                    "`https://docs.google.com/spreadsheets/d/<ID>/edit`\n"
                    "или просто `<ID>`.",
                    parse_mode="Markdown",
                )
                return

            user.google_sheets_spreadsheet_id = spreadsheet_id
            db.commit()

            sa_email_confirm = get_service_account_email()
            sa_confirm = sa_email_confirm if sa_email_confirm else "rich-uncle-scrooge-bot-648@rich-uncle-scrooge.iam.gserviceaccount.com"

            await update.message.reply_text(
                "✅ Сохранил твою таблицу.\n\n"
                "⚠️ **Не забудь дать доступ!**\n"
                "В Google Sheets нажми *Share* → добавь **Editor** для:\n"
                f"`{sa_confirm}`\n\n"
                "Команды:\n"
                "• `/sheets_export` — выгрузить данные в таблицу\n"
                "• `/sheets_import` — загрузить данные из таблицы",
                parse_mode="Markdown",
            )
            return

        sa_email = get_service_account_email()
        known_sa = "rich-uncle-scrooge-bot-648@rich-uncle-scrooge.iam.gserviceaccount.com"
        sa_line = f"`{sa_email}`" if sa_email else f"`{known_sa}`"

        if user.google_sheets_spreadsheet_id:
            await update.message.reply_text(
                f"📊 **Google Sheets подключена**\n\n"
                f"ID таблицы: `{user.google_sheets_spreadsheet_id}`\n\n"
                "Команды:\n"
                "• `/sheets_export` — выгрузить данные в таблицу\n"
                "• `/sheets_import` — загрузить данные из таблицы\n"
                "• `/sheets reset` — отключить таблицу",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "📄 **Google Sheets настройка**\n\n"
                "**Шаг 1:** Создай таблицу в Google Sheets\n\n"
                "**Шаг 2:** Нажми *Share* → добавь **Editor** для:\n"
                f"{sa_line}\n"
                "_(Без этого бот не сможет читать/писать в таблицу!)_\n\n"
                "**Шаг 3:** Скопируй ссылку на таблицу\n\n"
                "**Шаг 4:** Пришли сюда:\n"
                "`/sheets <ссылка_на_таблицу>`\n\n"
                "После настройки:\n"
                "• `/sheets_export` — выгрузить данные в таблицу\n"
                "• `/sheets_import` — загрузить данные из таблицы",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error(f"Error in sheets_command: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при работе с Google Sheets.")
    finally:
        db.close()


async def sheets_export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sheets_export command: export all data from bot DB to Google Sheets.

    Completely overwrites the Google Sheet with current bot data.
    """
    logger.info(f"sheets_export_command called by user {update.effective_user.id}")
    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)

        from services.google_sheets_client import is_configured, GoogleSheetsNotConfigured
        from services.sheets_sync import sync_user_to_sheets_async

        if not is_configured():
            await update.message.reply_text(
                "❌ Google Sheets интеграция на стороне бота не настроена.\n"
                "Попроси администратора настроить авторизацию."
            )
            return

        if not user.google_sheets_spreadsheet_id:
            await update.message.reply_text(
                "❌ Сначала подключи таблицу командой:\n"
                "`/sheets <ссылка_на_таблицу>`",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text("⏳ Выгружаю данные в Google Sheets...")

        try:
            url = await sync_user_to_sheets_async(db, user.id, user.google_sheets_spreadsheet_id)
        except GoogleSheetsNotConfigured as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
            return

        await update.message.reply_text(
            f"✅ Данные выгружены в таблицу.\n"
            f"Ссылка: {url}\n\n"
            "Листы:\n"
            "• **Балансы** — счета и балансы\n"
            "• **YYYY-MM** — операции по месяцам с итогами",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Error in sheets_export_command: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при выгрузке в Google Sheets.")
    finally:
        db.close()


async def sheets_import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sheets_import command: import all data from Google Sheets.

    Completely replaces all user data (accounts + transactions) with data from Sheets.
    Balances are imported as-is from the "Балансы" sheet.
    """
    logger.info(f"sheets_import_command called by user {update.effective_user.id}")
    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)

        from services.google_sheets_client import is_configured, GoogleSheetsNotConfigured
        from services.sheets_import import (
            parse_accounts_from_balances_sheet,
            parse_transactions_from_month_sheets,
        )
        from services.ledger import clear_user_data, create_account, create_transaction_raw

        if not is_configured():
            await update.message.reply_text(
                "❌ Google Sheets интеграция на стороне бота не настроена.\n"
                "Попроси администратора настроить авторизацию."
            )
            return

        if not user.google_sheets_spreadsheet_id:
            await update.message.reply_text(
                "❌ Сначала подключи таблицу командой:\n"
                "`/sheets <ссылка_на_таблицу>`",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text("⏳ Загружаю данные из Google Sheets...")

        try:
            imported_accounts = await asyncio.to_thread(
                parse_accounts_from_balances_sheet,
                user.google_sheets_spreadsheet_id,
            )

            imported_transactions = await asyncio.to_thread(
                parse_transactions_from_month_sheets,
                user.google_sheets_spreadsheet_id,
                db,
                user.id,
            )
        except GoogleSheetsNotConfigured as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
            return
        except Exception as e:
            logger.error(f"Failed to read from sheet: {e}", exc_info=True)
            await update.message.reply_text("❌ Не удалось прочитать таблицу. Проверь доступ и формат данных.")
            return

        if not imported_accounts:
            await update.message.reply_text(
                "❌ Не найдены счета в таблице.\n"
                "Убедись, что лист «Балансы» содержит данные."
            )
            return

        current_accounts = db.query(Account).filter(Account.user_id == user.id).count()
        current_transactions = db.query(Transaction).filter(Transaction.user_id == user.id).count()

        imported_data = {
            "accounts": [
                {
                    "name": acc.name,
                    "currency": acc.currency,
                    "initial_balance": str(acc.initial_balance),
                    "is_default": acc.is_default,
                }
                for acc in imported_accounts
            ],
            "transactions": [
                {
                    "account_name": tx.account_name,
                    "transaction_type": tx.transaction_type,
                    "amount": str(tx.amount),
                    "currency": tx.currency,
                    "category": tx.category,
                    "description": tx.description,
                    "operation_date": tx.operation_date.isoformat() if tx.operation_date else None,
                }
                for tx in imported_transactions
            ],
        }

        pending = PendingAction(
            user_id=user.id,
            action_type=ActionType.SHEETS_IMPORT,
            payload_json=json.dumps({"imported_data": imported_data}),
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            status=PendingStatus.PENDING
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)

        preview = f"""⚠️ **ИМПОРТ ИЗ GOOGLE SHEETS**

Это действие **УДАЛИТ ВСЕ** данные из бота и заменит их данными из таблицы!

📊 **Будет удалено из бота:**
  • Счетов: {current_accounts}
  • Операций: {current_transactions}

📥 **Будет импортировано из таблицы:**
  • Счетов: {len(imported_accounts)}
  • Операций: {len(imported_transactions)}

💡 Балансы будут взяты из таблицы как есть.

**Подтверди действие кнопками ниже.**"""

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"fin:confirm:{pending.id}"),
                InlineKeyboardButton("❌ Отменить", callback_data=f"fin:cancel:{pending.id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            preview,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error in sheets_import_command: {e}", exc_info=True)
        await update.message.reply_text("Произошла ошибка при импорте из Google Sheets.")
    finally:
        db.close()
