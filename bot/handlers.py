"""Bot handlers."""
import asyncio
import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes
from sqlalchemy.orm import Session

from db.models import User, Account, PendingAction, ActionType, PendingStatus
from db.session import SessionLocal
from services.ledger import (
    get_or_create_user, find_account_by_name, add_income, add_expense,
    transfer, create_account, delete_account, rename_account, set_default_account,
    list_user_transactions, get_transaction_by_row_number, update_transaction, delete_transaction_by_id
)
from services.reports import get_report, format_report_text
from services.insights import get_insight, format_insight_text
from llm.parser import parse_message
from utils.dates import now_in_timezone, parse_period, format_operation_date
from utils.money import format_amount

logger = logging.getLogger(__name__)

# --- Telegram send/edit reliability ---
# Sometimes Telegram API calls fail transiently (DNS hiccups, short disconnects).
# Without a retry, the user sees "bot doesn't answer" even though the update was processed.
_ORIGINAL_MESSAGE_REPLY_TEXT = Message.reply_text
_ORIGINAL_MESSAGE_EDIT_TEXT = Message.edit_text
_ORIGINAL_CALLBACK_EDIT_MESSAGE_TEXT = CallbackQuery.edit_message_text


async def _retry_telegram_call(coro_factory, *, attempts: int = 4):
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except (TimedOut, NetworkError) as e:
            last_exc = e
            # Exponential-ish backoff: 0.5s, 1s, 2s, 4s
            await asyncio.sleep(0.5 * (2**attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError("Telegram call failed with unknown error")


async def _message_reply_text_retry(self: Message, *args, **kwargs):
    return await _retry_telegram_call(lambda: _ORIGINAL_MESSAGE_REPLY_TEXT(self, *args, **kwargs))


async def _message_edit_text_retry(self: Message, *args, **kwargs):
    return await _retry_telegram_call(lambda: _ORIGINAL_MESSAGE_EDIT_TEXT(self, *args, **kwargs))


async def _callback_edit_message_text_retry(self: CallbackQuery, *args, **kwargs):
    return await _retry_telegram_call(
        lambda: _ORIGINAL_CALLBACK_EDIT_MESSAGE_TEXT(self, *args, **kwargs)
    )


# Monkeypatch PTB convenience methods used throughout handlers.py
Message.reply_text = _message_reply_text_retry  # type: ignore[assignment]
Message.edit_text = _message_edit_text_retry  # type: ignore[assignment]
CallbackQuery.edit_message_text = _callback_edit_message_text_retry  # type: ignore[assignment]


def get_db() -> Session:
    """Get database session."""
    return SessionLocal()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    logger.info(f"start_command called by user {update.effective_user.id}")
    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)
        
        accounts = db.query(Account).filter(Account.user_id == user.id).all()
        
        if not accounts:
            await update.message.reply_text(
                "💰 Дядя Скрудж к вашим услугам!\n\n"
                "Буду считать твои деньги и следить, чтобы ни одна монетка не пропала.\n\n"
                "Для начала создай счёт:\n"
                "«создай счет наличка rub» или «добавь счет карта usd»"
            )
        else:
            accounts_text = "\n".join([
                f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}"
                for acc in accounts
            ])
            await update.message.reply_text(
                f"💰 С возвращением! Твои счета:\n{accounts_text}\n\n"
                "Рассказывай о доходах и расходах — всё запишу.\n\n"
                "Примеры:\n"
                "• кофе 320\n"
                "• +50000 зп\n"
                "• переведи 10к с карты на нал\n"
                "• отчет за ноябрь"
            )
    except Exception as e:
        logger.error(f"Error in start_command: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")
    finally:
        db.close()


async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /accounts command."""
    db = get_db()
    try:
        user = db.query(User).filter(User.tg_user_id == update.effective_user.id).first()
        if not user:
            await update.message.reply_text("Сначала используй /start")
            return
        
        accounts = db.query(Account).filter(Account.user_id == user.id).all()
        
        if not accounts:
            await update.message.reply_text("💰 Пока пусто. Создай первый счёт!")
        else:
            lines = ["💰 Твои счета:\n"]
            for acc in accounts:
                default_mark = " ⭐" if acc.is_default else ""
                lines.append(
                    f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}{default_mark}"
                )
            await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.error(f"Error in accounts_command: {e}")
        await update.message.reply_text("Произошла ошибка.")
    finally:
        db.close()


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report command."""
    db = get_db()
    try:
        user = db.query(User).filter(User.tg_user_id == update.effective_user.id).first()
        if not user:
            await update.message.reply_text("Сначала используй /start")
            return
        
        # Default to current month
        report = get_report(db, user.id, period_preset="month", user_timezone=user.timezone)
        text = format_report_text(report, user.timezone)
        await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Error in report_command: {e}")
        await update.message.reply_text("Произошла ошибка при формировании отчёта.")
    finally:
        db.close()


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

        # Step 1: auth must be configured on bot side
        if not is_configured():
            await update.message.reply_text(
                "❌ Google Sheets интеграция на стороне бота не настроена.\n"
                "Попроси администратора настроить авторизацию (service account или OAuth)."
            )
            return

        # Step 2: allow user to set/reset their spreadsheet id
        args = (context.args or []) if context else []
        if args:
            raw = " ".join(args).strip()
            if raw.lower() in {"reset", "off", "disable", "удалить", "сброс"}:
                user.google_sheets_spreadsheet_id = None
                db.commit()
                await update.message.reply_text("✅ Готово. Привязка Google Sheets удалена.")
                return

            # Accept full URL or plain id
            import re

            m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", raw)
            spreadsheet_id = m.group(1) if m else raw
            spreadsheet_id = spreadsheet_id.strip()

            # Basic sanity check
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

        # Step 3: show current status or instructions
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
        from db.models import Account, Transaction

        # Check auth configured
        if not is_configured():
            await update.message.reply_text(
                "❌ Google Sheets интеграция на стороне бота не настроена.\n"
                "Попроси администратора настроить авторизацию."
            )
            return

        # Check user has spreadsheet configured
        if not user.google_sheets_spreadsheet_id:
            await update.message.reply_text(
                "❌ Сначала подключи таблицу командой:\n"
                "`/sheets <ссылка_на_таблицу>`",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text("⏳ Загружаю данные из Google Sheets...")

        try:
            # Parse accounts from "Балансы" sheet
            imported_accounts = await asyncio.to_thread(
                parse_accounts_from_balances_sheet,
                user.google_sheets_spreadsheet_id,
            )
            
            # Parse transactions from all YYYY-MM sheets
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

        # Get current counts for preview
        current_accounts = db.query(Account).filter(Account.user_id == user.id).count()
        current_transactions = db.query(Transaction).filter(Transaction.user_id == user.id).count()

        # Serialize imported data for pending action
        import json
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

        # Create pending action
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

        # Build preview message
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = """💰 *Дядя Скрудж — справка*

Что это: бот для учёта личных финансов в Telegram. Пишешь как обычно — бот сам понимает что произошло (расход/доход/перевод), сумму, счёт, категорию и дату. Доступна интеграция с Google Sheets.

Важно: любые изменения (операции и счета) бот сначала показывает на подтверждение. Запись происходит только после кнопки ✅ Подтвердить.

⸻

🚀 *Как начать*
1. Создай счета (например: карта, наличка, крипта)
2. Выбери главный счёт (по умолчанию)
_Это счёт, который бот использует автоматически, если ты не указал, откуда списать или куда зачислить деньги._
3. Просто записывай операции обычным языком

💡 _Полезная привычка: записывать траты сразу после покупки._

⸻

*💳 Счета*

Создать:
• создай счет наличка rub
• создай счет тинькофф usd 5000 _(с балансом)_

Удалить / переименовать:
• удали счет юмани
• переименуй счет тинькофф в тиньк

Сделать главным:
• главный счет тинькофф

Посмотреть:
• мои счета • покажи счета • баланс

⸻

*💸 Расходы*
• кофе 320
• такси 500
• продукты 1500

_Если счёт не указан — списание будет с главного счёта._

⸻

*💰 Доходы*
• +50000 зарплата
• получил 10000 возврат
• зп 150000

⸻

*🔄 Переводы между счетами*
• переведи 10к с тинька на нал
• перекинь 5000 с карты на наличку
• кросс-валютный: перекинь с рублей 50к на крипту 600$

⸻

*📦 Несколько операций сразу*
• кофе 300, такси 500, обед 400
• зп 100к и кофе 300
• создай счет карта rub и счет крипта usdt
• удали 3 и 5

⸻

*📊 Отчёты и история*

Отчёты:
• отчет за ноябрь
• статистика за неделю

_В отчёте: доходы/расходы/сальдо, сумма на всех счетах, и откуда пришли / куда ушли по категориям._

История операций:
• история
• покажи расходы за декабрь

⸻

*📄 Google Sheets*

**Настройка:**
1) Создай таблицу в Google Sheets
2) "Share" → добавь **Editor** для:
   `rich-uncle-scrooge-bot-648@rich-uncle-scrooge.iam.gserviceaccount.com`
3) Пришли в бот: `/sheets <ссылка_на_таблицу>`

**Команды:**
• `/sheets` — статус и инструкции
• `/sheets <ссылка>` — подключить таблицу
• `/sheets reset` — отключить
• `/sheets_export` — выгрузить все данные в таблицу
• `/sheets_import` — загрузить все данные из таблицы

**Как это работает:**
• `/sheets_export` — полностью перезаписывает таблицу данными из бота
• `/sheets_import` — полностью заменяет данные в боте данными из таблицы

⚠️ **Важно:** синхронизация НЕ автоматическая! Используй команды вручную.

**Рабочий процесс:**
1) `/sheets_export` — выгрузи данные
2) Редактируй таблицу (меняй балансы, добавляй операции)
3) `/sheets_import` — загрузи изменения обратно

**Структура таблицы:**
• **Балансы** — счета, валюты, балансы
• **YYYY-MM** — операции по месяцам с итогами

⸻

*✏️ Редактирование и удаление операций*
• измени 3 сумма 500
• редактировать 5 категория еда
• удали запись 5

⸻

*🔍 Аналитика "почему так много"*
• почему так много на еду в этом месяце
• куда ушли деньги в декабре

_Бот объяснит, что дало основной вклад (категории, крупные операции, пики по дням)._

⸻

*🎤 Голосовые сообщения*
Можешь просто надиктовать — бот распознает речь и обработает как текст.

⸻

✅ _Категории определяются автоматически. Все операции требуют подтверждения кнопкой._"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages."""
    if not update.message or not update.message.text:
        return
    
    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)
        
        # Check if user has pending actions (they might be trying to confirm via text)
        pending = db.query(PendingAction).filter(
            PendingAction.user_id == user.id,
            PendingAction.status == PendingStatus.PENDING,
            PendingAction.expires_at > datetime.utcnow()
        ).first()
        
        if pending:
            # User has pending action, remind them to use buttons
            text_lower = update.message.text.lower()
            if text_lower in ["ок", "да", "подтвердить", "yes", "ok", "подтверждаю"]:
                await update.message.reply_text(
                    "Нажми кнопку ниже: ✅ Подтвердить или ❌ Отменить."
                )
                db.close()
                return
    finally:
        try:
            db.close()
        except:
            pass
    
    # Process the text using shared logic
    await process_user_text(update, context, update.message.text)


async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages - transcribe and process as text."""
    from services.speech import transcribe_telegram_voice
    
    voice = update.message.voice
    if not voice:
        return
    
    # Send "processing" message
    processing_msg = await update.message.reply_text("🎤 Распознаю голосовое сообщение...")
    
    try:
        # Transcribe voice message
        text = await transcribe_telegram_voice(context.bot, voice.file_id)
        
        if not text or not text.strip():
            await processing_msg.edit_text("❌ Не удалось распознать речь. Попробуй ещё раз или напиши текстом.")
            return
        
        text = text.strip()
        
        # Show transcribed text
        await processing_msg.edit_text(f"🎤 Распознано: _{text}_", parse_mode="Markdown")
        
        # Process the transcribed text using core logic
        await process_user_text(update, context, text)
        
    except Exception as e:
        logger.error(f"Error in voice_message_handler: {e}", exc_info=True)
        try:
            await processing_msg.edit_text("❌ Произошла ошибка при обработке голосового сообщения.")
        except:
            pass


async def process_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Process user text message (shared between text and voice handlers)."""
    db = get_db()
    message_sent = False
    
    try:
        user_id = update.effective_user.id
        
        # Get or create user
        user = get_or_create_user(db, user_id)
        
        # Check if user has pending clarification
        pending_clarification = db.query(PendingAction).filter(
            PendingAction.user_id == user.id,
            PendingAction.action_type == ActionType.CLARIFICATION,
            PendingAction.status == PendingStatus.PENDING,
            PendingAction.expires_at > datetime.utcnow()
        ).order_by(PendingAction.created_at.desc()).first()
        
        if pending_clarification:
            # User is answering a clarification question
            import json
            payload = json.loads(pending_clarification.payload_json)
            original_message = payload.get("original_message", "")
            
            # Check if answer is an account name
            answer_lower = text.lower().strip()
            matching_account = None
            for acc in db.query(Account).filter(Account.user_id == user.id).all():
                if acc.name.lower() in answer_lower or answer_lower in acc.name.lower():
                    matching_account = acc
                    break
            
            if matching_account:
                # User specified account name directly
                combined_message = f"{original_message} со счёта {matching_account.name}"
            else:
                # Combine original message with clarification answer
                combined_message = f"{original_message}. {text}"
            
            # Mark clarification as completed
            pending_clarification.status = PendingStatus.CONFIRMED
            db.commit()
            
            # Parse combined message
            text = combined_message
        
        # Get user's accounts FIRST
        accounts_list = db.query(Account).filter(Account.user_id == user.id).all()
        
        # SYNC default account BEFORE parsing
        default_account = None
        
        # 1. Check user.default_account_id
        if user.default_account_id:
            default_account = db.query(Account).filter(Account.id == user.default_account_id).first()
        
        # 2. Try to find by is_default flag if user.default_account_id is NULL
        if not default_account:
            default_account = db.query(Account).filter(
                Account.user_id == user.id,
                Account.is_default == True
            ).first()
            
            # Sync user.default_account_id with account.is_default
            if default_account:
                user.default_account_id = default_account.id
                db.commit()
                logger.info(f"Synced default_account_id={default_account.id} for user {user.id}")
        
        # 3. If no default account set but user has exactly one account, use it
        if not default_account and len(accounts_list) == 1:
            default_account = accounts_list[0]
            user.default_account_id = default_account.id
            default_account.is_default = True
            db.commit()
            logger.info(f"Auto-set default account {default_account.name} for user {user.id}")
        
        # NOW parse message with LLM (with correct default_account)
        accounts_for_llm = [
            {"name": acc.name, "currency": acc.currency, "balance": float(acc.balance)}
            for acc in accounts_list
        ]
        
        default_account_name = default_account.name if default_account else None
        logger.info(f"Parsing message with default_account={default_account_name}")
        
        llm_response = await parse_message(
            text,
            accounts_for_llm,
            default_account_name,
            user.timezone
        )
        
        logger.info(f"Parsed intent: {llm_response.intent}, confidence: {llm_response.confidence}")
        
        # Handle low confidence or errors
        if llm_response.confidence < 0.5:
            await update.message.reply_text("Не понял. Попробуй написать по-другому или используй /help для примеров.")
            message_sent = True
            return
        
        if llm_response.intent == "unknown":
            await update.message.reply_text("Не понял. Попробуй написать по-другому или используй /help для примеров.")
            message_sent = True
            return
        
        if llm_response.intent == "clarify":
            clarify_q = llm_response.data.clarify_question or "Уточни, пожалуйста."
            
            # Save original message for context
            import json
            pending = PendingAction(
                user_id=user.id,
                action_type=ActionType.CLARIFICATION,
                payload_json=json.dumps({
                    "original_message": text,
                    "question": clarify_q,
                    "llm_data": llm_response.data.model_dump() if llm_response.data else {}
                }),
                expires_at=datetime.utcnow() + timedelta(minutes=10),
                status=PendingStatus.PENDING
            )
            db.add(pending)
            db.commit()
            
            await update.message.reply_text(clarify_q)
            message_sent = True
            return
        
        # Handle batch operations (multiple operations in one message)
        if llm_response.intent == "batch":
            await handle_batch_intent(db, update, user, llm_response, accounts_list, default_account)
            message_sent = True
            return
        
        if llm_response.intent == "report":
            await handle_report_intent(db, update, user, llm_response)
            message_sent = True
            return
        
        if llm_response.intent == "show_accounts":
            await handle_show_accounts_intent(db, update, user)
            message_sent = True
            return
        
        if llm_response.intent == "insight":
            await handle_insight_intent(db, update, user, llm_response)
            message_sent = True
            return
        
        if llm_response.intent == "list_transactions":
            await handle_list_transactions_intent(db, update, user, llm_response)
            message_sent = True
            return
        
        if llm_response.intent == "edit_transaction":
            await handle_edit_transaction_intent(db, update, user, llm_response)
            message_sent = True
            return
        
        if llm_response.intent == "delete_transaction":
            await handle_delete_transaction_intent(db, update, user, llm_response)
            message_sent = True
            return
        
        # All other intents require confirmation
        await handle_mutation_intent(db, update, user, llm_response)
        message_sent = True
        
    except Exception as e:
        logger.error(f"Error in process_user_text: {e}", exc_info=True)
        if not message_sent:
            try:
                await update.message.reply_text("Произошла ошибка. Попробуй позже.")
            except:
                pass
    finally:
        try:
            db.close()
        except:
            pass


async def handle_batch_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response,
    accounts_list: list,
    default_account: str
):
    """Handle batch of multiple operations."""
    from schemas.llm_schema import LLMResponse, LLMResponseData
    
    operations = llm_response.operations or []
    
    if not operations:
        await update.message.reply_text("Не удалось распознать операции.")
        return
    
    # Separate mutation operations (need confirmation) from read-only operations
    mutation_intents = [
        "income", "expense", "transfer", 
        "account_add", "account_delete", "account_rename", 
        "set_default_account", "edit_transaction", "delete_transaction"
    ]
    
    mutation_ops = []
    read_ops = []
    
    for op in operations:
        if op.intent in mutation_intents:
            mutation_ops.append(op)
        else:
            read_ops.append(op)
    
    # Process read-only operations immediately (show_accounts, report, etc.)
    for op in read_ops:
        # Create a fake LLMResponse for compatibility
        fake_response = LLMResponse(
            intent=op.intent,
            confidence=0.9,
            data=op.data,
            errors=[]
        )
        
        if op.intent == "report":
            await handle_report_intent(db, update, user, fake_response)
        elif op.intent == "show_accounts":
            await handle_show_accounts_intent(db, update, user)
        elif op.intent == "list_transactions":
            await handle_list_transactions_intent(db, update, user, fake_response)
        elif op.intent == "insight":
            await handle_insight_intent(db, update, user, fake_response)
    
    # If no mutation operations, we're done
    if not mutation_ops:
        return
    
    # Collect accounts that will be created in this batch
    accounts_to_create = set()
    for op in mutation_ops:
        if op.intent == "account_add":
            acc_new = getattr(op.data, 'account_new', None)
            if acc_new and getattr(acc_new, 'name', None):
                accounts_to_create.add(acc_new.name.lower())
    
    # Validate all mutation operations
    all_errors = []
    validated_ops = []
    
    for i, op in enumerate(mutation_ops, 1):
        errors = validate_mutation_data(db, user, op.intent, op.data)
        
        # Filter out "account not found" errors if account will be created in this batch
        filtered_errors = []
        for error in errors:
            # Check if error is about missing account
            is_account_not_found = "не найден" in error.lower()
            if is_account_not_found:
                # Extract account name from error message
                account_mentioned = False
                for acc_name in accounts_to_create:
                    if acc_name in error.lower():
                        account_mentioned = True
                        break
                # Skip error if account will be created
                if not account_mentioned:
                    filtered_errors.append(error)
            else:
                filtered_errors.append(error)
        
        if filtered_errors:
            all_errors.append(f"Операция {i}: " + ", ".join(filtered_errors))
        else:
            validated_ops.append(op)
    
    # Only show errors if there are any after filtering
    if all_errors:
        error_text = "⚠️ Ошибки в операциях:\n" + "\n".join(all_errors)
        await update.message.reply_text(error_text)
        if not validated_ops:
            return
    
    # Build preview for all valid mutations
    preview_lines = ["📋 *Несколько операций:*\n"]
    
    for i, op in enumerate(validated_ops, 1):
        preview_line = build_single_operation_preview(op.intent, op.data, user.timezone)
        preview_lines.append(f"{i}. {preview_line}")
    
    preview_lines.append("\nПодтверди все операции кнопками ниже.")
    preview_text = "\n".join(preview_lines)
    
    # Store pending action with all operations
    from datetime import timedelta
    
    operations_payload = {
        "intent": "batch",
        "operations": [
            {"intent": op.intent, "data": op.data.model_dump(exclude_none=True)} 
            for op in validated_ops
        ]
    }
    
    pending = PendingAction(
        user_id=user.id,
        action_type=ActionType.BATCH,
        payload_json=operations_payload,
        expires_at=datetime.utcnow() + timedelta(minutes=5)
    )
    db.add(pending)
    db.commit()
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить все", callback_data=f"fin:confirm:{pending.id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"fin:cancel:{pending.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    sent_message = await update.message.reply_text(
        preview_text, 
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    # Update pending action with message_id
    pending.preview_message_id = sent_message.message_id
    db.commit()


def build_single_operation_preview(intent: str, data, user_timezone: str) -> str:
    """Build preview text for a single operation in batch."""
    from utils.dates import format_operation_date
    from datetime import datetime
    
    if intent == "income":
        amount_str = format_amount(data.amount, data.currency or "RUB")
        category = data.category or "Без категории"
        return f"💰 +{amount_str} ({category})"
    
    elif intent == "expense":
        amount_str = format_amount(data.amount, data.currency or "RUB")
        category = data.category or "Без категории"
        desc = f" — {data.description}" if data.description else ""
        return f"💸 −{amount_str} ({category}){desc}"
    
    elif intent == "transfer":
        from_acc = data.from_account_name or "?"
        to_acc = data.to_account_name or "?"
        amount_str = format_amount(data.amount, data.currency or "RUB")
        return f"🔄 {from_acc} → {to_acc}: {amount_str}"
    
    elif intent == "account_add":
        name = data.account_new.name if data.account_new else "?"
        currency = data.account_new.currency if data.account_new else "RUB"
        balance = data.account_new.initial_balance if data.account_new else 0
        if balance > 0:
            return f"💳 Создать «{name}» ({currency}, {format_amount(balance, currency)})"
        return f"💳 Создать «{name}» ({currency})"
    
    elif intent == "account_delete":
        return f"🗑️ Удалить «{data.account_name}»"
    
    elif intent == "clear_all_data":
        return f"⚠️ УДАЛИТЬ ВСЕ ДАННЫЕ (счета + операции)"
    
    elif intent == "account_rename":
        return f"✏️ Переименовать «{data.account_old_name}» → «{data.account_new_name}»"
    
    elif intent == "set_default_account":
        return f"⭐ Сделать «{data.account_name}» основным"
    
    elif intent == "edit_transaction":
        changes = []
        if data.new_amount is not None:
            changes.append(f"сумма: {data.new_amount}")
        if data.new_category:
            changes.append(f"категория: {data.new_category}")
        if data.new_description:
            changes.append(f"описание: {data.new_description}")
        return f"✏️ Изменить #{data.transaction_id}: {', '.join(changes)}"
    
    elif intent == "delete_transaction":
        return f"🗑️ Удалить запись #{data.transaction_id}"
    
    return f"❓ {intent}"


async def handle_report_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle report intent (read-only, no confirmation)."""
    data = llm_response.data
    period = data.period
    
    report = get_report(
        db,
        user.id,
        period_preset=period.preset if period else None,
        from_date=period.from_date if period else None,
        to_date=period.to if period else None,
        user_timezone=user.timezone
    )
    
    text = format_report_text(report, user.timezone)
    await update.message.reply_text(text)


async def handle_show_accounts_intent(
    db: Session,
    update: Update,
    user: User
):
    """Handle show_accounts intent (read-only, no confirmation)."""
    accounts = db.query(Account).filter(Account.user_id == user.id).all()
    
    if not accounts:
        await update.message.reply_text(
            "У тебя пока нет счетов. Создай первый:\n"
            "\"создай счет наличка rub\""
        )
        return
    
    lines = ["💳 Твои счета:\n"]
    total_by_currency = {}
    
    # Determine which account is default
    # Priority: user.default_account_id > acc.is_default > first account
    default_account_id = user.default_account_id
    if not default_account_id:
        # Check if any account has is_default=True
        for acc in accounts:
            if acc.is_default:
                default_account_id = acc.id
                break
        # If still no default, use first account
        if not default_account_id and accounts:
            default_account_id = accounts[0].id
    
    for acc in accounts:
        is_default = (acc.id == default_account_id)
        default_mark = " ⭐ (основной)" if is_default else ""
        lines.append(
            f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}{default_mark}"
        )
        if acc.currency not in total_by_currency:
            total_by_currency[acc.currency] = Decimal("0")
        total_by_currency[acc.currency] += acc.balance
    
    if len(accounts) > 1:
        lines.append("\n📊 Итого:")
        for currency, total in total_by_currency.items():
            lines.append(f"  {format_amount(total, currency)}")
    
    await update.message.reply_text("\n".join(lines))


async def handle_list_transactions_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle list_transactions intent (read-only, shows numbered list)."""
    from datetime import date
    data = llm_response.data
    period = data.period
    transaction_type = data.transaction_type
    
    # Parse period
    from_date = None
    to_date = None
    if period:
        if period.from_date:
            try:
                from_date = date.fromisoformat(period.from_date)
            except:
                pass
        if period.to:  # Fixed: period.to is correct, not period.to_date
            try:
                to_date = date.fromisoformat(period.to)
            except:
                pass
    
    # Get transactions
    transactions = list_user_transactions(
        db, user.id,
        from_date=from_date,
        to_date=to_date,
        transaction_type=transaction_type,
        limit=50
    )
    
    if not transactions:
        await update.message.reply_text("📝 Нет операций за указанный период.")
        return
    
    # Format header
    period_str = ""
    if from_date and to_date:
        period_str = f" за {from_date.strftime('%d.%m.%Y')}–{to_date.strftime('%d.%m.%Y')}"
    elif from_date:
        period_str = f" с {from_date.strftime('%d.%m.%Y')}"
    elif to_date:
        period_str = f" до {to_date.strftime('%d.%m.%Y')}"
    
    type_str = ""
    if transaction_type == "income":
        type_str = " (доходы)"
    elif transaction_type == "expense":
        type_str = " (расходы)"
    
    lines = [f"📝 История операций{period_str}{type_str}:\n"]
    
    for row_num, tx in transactions:
        # Type emoji
        if tx.type.value == "income":
            emoji = "💰"
            sign = "+"
        elif tx.type.value == "expense":
            emoji = "💸"
            sign = "-"
        else:
            emoji = "🔄"
            sign = ""
        
        # Date
        date_str = tx.operation_date.strftime("%d.%m") if tx.operation_date else ""
        
        # Category/subcategory/description
        cat_parts = []
        if tx.category:
            cat_parts.append(tx.category)
        if tx.subcategory:
            cat_parts.append(tx.subcategory)
        if tx.description:
            cat_parts.append(tx.description)
        desc = " — " + " / ".join(cat_parts) if cat_parts else ""
        
        # Account
        account_name = ""
        if tx.account_id:
            acc = db.query(Account).filter(Account.id == tx.account_id).first()
            if acc:
                account_name = f" ({acc.name})"
        
        lines.append(
            f"{row_num}. {emoji} {date_str} {sign}{format_amount(tx.amount, tx.currency)}{account_name}{desc}"
        )
    
    lines.append("\n💡 Для редактирования: \"измени запись 3 сумма 500\"")
    lines.append("💡 Для удаления: \"удали запись 3\"")
    
    await update.message.reply_text("\n".join(lines))


async def handle_edit_transaction_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle edit_transaction intent (requires confirmation)."""
    data = llm_response.data
    
    if not data.transaction_id:
        await update.message.reply_text("❌ Укажи номер записи для редактирования.")
        return
    
    # Find the transaction
    tx = get_transaction_by_row_number(db, user.id, data.transaction_id)
    
    if not tx:
        await update.message.reply_text(f"❌ Запись #{data.transaction_id} не найдена. Посмотри список: \"история операций\"")
        return
    
    # Build preview
    changes = []
    if data.new_amount:
        changes.append(f"Сумма: {format_amount(tx.amount, tx.currency)} → {format_amount(Decimal(str(data.new_amount)), tx.currency)}")
    if data.new_category:
        old_cat = tx.category or "Без категории"
        changes.append(f"Категория: {old_cat} → {data.new_category}")
    if data.new_description:
        old_desc = tx.description or "—"
        changes.append(f"Описание: {old_desc} → {data.new_description}")
    
    if not changes:
        await update.message.reply_text("❌ Укажи, что изменить: сумму, категорию или описание.")
        return
    
    # Create pending action with proper structure for handle_confirm
    pending = PendingAction(
        user_id=user.id,
        action_type=ActionType.EDIT_TRANSACTION,
        payload_json=json.dumps({
            "intent": "edit_transaction",
            "data": {
                "transaction_id": tx.id,
                "new_amount": data.new_amount,
                "new_category": data.new_category,
                "new_description": data.new_description
            }
        }),
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        status=PendingStatus.PENDING
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    
    # Preview text
    emoji = "💰" if tx.type.value == "income" else "💸"
    current_desc = tx.description or tx.category or "—"
    
    preview = f"""✏️ Редактирование записи #{data.transaction_id}:

Текущие данные:
  {emoji} {format_amount(tx.amount, tx.currency)}
  📝 {current_desc}

Изменения:
  """ + "\n  ".join(changes) + """

Подтверди действие кнопками ниже."""
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"fin:confirm:{pending.id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"fin:cancel:{pending.id}")
        ]
    ]
    
    await update.message.reply_text(preview, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_delete_transaction_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle delete_transaction intent (requires confirmation)."""
    data = llm_response.data
    
    if not data.transaction_id:
        await update.message.reply_text("❌ Укажи номер записи для удаления.")
        return
    
    # Find the transaction
    tx = get_transaction_by_row_number(db, user.id, data.transaction_id)
    
    if not tx:
        await update.message.reply_text(f"❌ Запись #{data.transaction_id} не найдена. Посмотри список: \"история операций\"")
        return
    
    # Create pending action with proper structure for handle_confirm
    pending = PendingAction(
        user_id=user.id,
        action_type=ActionType.DELETE_TRANSACTION,
        payload_json=json.dumps({
            "intent": "delete_transaction",
            "data": {
                "transaction_id": tx.id
            }
        }),
        expires_at=datetime.utcnow() + timedelta(minutes=10),
        status=PendingStatus.PENDING
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    
    # Preview text
    emoji = "💰" if tx.type.value == "income" else "💸"
    current_desc = tx.description or tx.category or "—"
    date_str = tx.operation_date.strftime("%d.%m.%Y") if tx.operation_date else ""
    
    preview = f"""🗑️ Удаление записи #{data.transaction_id}:

{emoji} {date_str} — {format_amount(tx.amount, tx.currency)}
📝 {current_desc}

⚠️ Баланс счёта будет скорректирован.

Подтверди удаление кнопками ниже."""
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Удалить", callback_data=f"fin:confirm:{pending.id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"fin:cancel:{pending.id}")
        ]
    ]
    
    await update.message.reply_text(preview, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_insight_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle insight intent (read-only, with action buttons)."""
    data = llm_response.data
    insight_query = data.insight_query
    
    # LLM might return insight fields directly in data (not nested in insight_query)
    # Try to extract from data fields if insight_query is None
    if not insight_query:
        # Check if insight fields are present directly in data
        metric = data.metric
        if not metric:
            await update.message.reply_text("Не удалось понять вопрос. Попробуй переформулировать.")
            return
        
        # Build insight query from data fields
        period = data.period
        period_preset = period.preset if period else None
        from_date = period.from_date if period else None
        to_date = period.to if period else None
        
        insight = get_insight(
            db,
            user.id,
            metric=metric,
            category=data.category,
            period_preset=period_preset,
            from_date=from_date,
            to_date=to_date,
            compare_to=data.compare_to or "prev_month",
            account_name=data.account_name,
            currency=data.currency,
            user_timezone=user.timezone
        )
    else:
        period = insight_query.period
        
        insight = get_insight(
            db,
            user.id,
            metric=insight_query.metric,
            category=insight_query.category,
            period_preset=period.preset if period else None,
            from_date=period.from_date if period else None,
            to_date=period.to if period else None,
            compare_to=insight_query.compare_to or "prev_month",
            account_name=insight_query.account_name,
            currency=insight_query.currency,
            user_timezone=user.timezone
        )
    
    text = format_insight_text(insight, user.timezone)
    
    # Add action buttons
    keyboard = [
        [
            InlineKeyboardButton("📌 Топ операций", callback_data=f"fin:insight:top:{user.id}"),
            InlineKeyboardButton("📆 Сравнить с прошлым месяцем", callback_data=f"fin:insight:compare_prev_month:{user.id}")
        ],
        [
            InlineKeyboardButton("📊 Показать по дням", callback_data=f"fin:insight:byday:{user.id}"),
            InlineKeyboardButton("🏷️ Уточнить категорию", callback_data=f"fin:insight:category:{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup)


def validate_mutation_data(db: Session, user: User, intent: str, data) -> list:
    """Validate data for mutation operations. Returns list of errors."""
    errors = []
    
    if intent == "income":
        if not data.amount:
            errors.append("Не указана сумма")
        if not getattr(data, 'operation_date', None):
            errors.append("Не указана дата")
        if not getattr(data, 'account_name', None) and not user.default_account_id:
            errors.append("Не указан счёт, а дефолтного нет")
        elif getattr(data, 'account_name', None):
            acc = find_account_by_name(db, user.id, data.account_name)
            if not acc:
                errors.append(f"Счёт «{data.account_name}» не найден")
        
        # Validate currency mismatch
        account_name = getattr(data, 'account_name', None)
        if account_name:
            acc = find_account_by_name(db, user.id, account_name)
        else:
            acc = db.query(Account).filter(Account.id == user.default_account_id).first()
        
        if acc and getattr(data, 'currency', None):
            user_currency = data.currency.upper() if data.currency else None
            if user_currency and user_currency != acc.currency.upper():
                errors.append(
                    f"Указана валюта {user_currency}, но счёт «{acc.name}» в {acc.currency}. "
                    f"Уточни счёт или убери валюту из текста."
                )
    
    elif intent == "expense":
        if not data.amount:
            errors.append("Не указана сумма")
        if not getattr(data, 'operation_date', None):
            errors.append("Не указана дата")
        if not getattr(data, 'account_name', None) and not user.default_account_id:
            errors.append("Не указан счёт, а дефолтного нет")
        elif getattr(data, 'account_name', None):
            acc = find_account_by_name(db, user.id, data.account_name)
            if not acc:
                errors.append(f"Счёт «{data.account_name}» не найден")
        
        # Validate currency mismatch
        account_name = getattr(data, 'account_name', None)
        if account_name:
            acc = find_account_by_name(db, user.id, account_name)
        else:
            acc = db.query(Account).filter(Account.id == user.default_account_id).first()
        
        if acc and getattr(data, 'currency', None):
            user_currency = data.currency.upper() if data.currency else None
            if user_currency and user_currency != acc.currency.upper():
                errors.append(
                    f"Указана валюта {user_currency}, но счёт «{acc.name}» в {acc.currency}. "
                    f"Уточни счёт или убери валюту из текста."
                )
    
    elif intent == "transfer":
        if not data.amount:
            errors.append("Не указана сумма")
        if not getattr(data, 'from_account_name', None):
            errors.append("Не указан счёт-источник")
        if not getattr(data, 'to_account_name', None):
            errors.append("Не указан счёт-получатель")
        if not getattr(data, 'operation_date', None):
            errors.append("Не указана дата")
        
        if getattr(data, 'from_account_name', None) and getattr(data, 'to_account_name', None):
            from_acc = find_account_by_name(db, user.id, data.from_account_name)
            to_acc = find_account_by_name(db, user.id, data.to_account_name)
            
            if not from_acc:
                errors.append(f"Счёт «{data.from_account_name}» не найден")
            if not to_acc:
                errors.append(f"Счёт «{data.to_account_name}» не найден")
    
    elif intent == "account_add":
        acc_new = getattr(data, 'account_new', None)
        if not acc_new or not getattr(acc_new, 'name', None):
            errors.append("Не указано название счёта")
        elif not acc_new or not getattr(acc_new, 'currency', None):
            errors.append("Не указана валюта")
        else:
            existing = find_account_by_name(db, user.id, acc_new.name, exact_only=True)
            if existing:
                errors.append(f"Счёт «{acc_new.name}» уже существует")
    
    elif intent == "account_delete":
        if not getattr(data, 'account_name', None):
            errors.append("Не указан счёт для удаления")
        else:
            acc = find_account_by_name(db, user.id, data.account_name)
            if not acc:
                errors.append(f"Счёт «{data.account_name}» не найден")
    
    elif intent == "account_rename":
        if not getattr(data, 'account_old_name', None):
            errors.append("Не указан счёт для переименования")
        else:
            acc = find_account_by_name(db, user.id, data.account_old_name)
            if not acc:
                errors.append(f"Счёт «{data.account_old_name}» не найден")
        if not getattr(data, 'account_new_name', None):
            errors.append("Не указано новое название")
    
    elif intent == "set_default_account":
        if not getattr(data, 'account_name', None):
            errors.append("Не указан счёт")
        else:
            acc = find_account_by_name(db, user.id, data.account_name)
            if not acc:
                errors.append(f"Счёт «{data.account_name}» не найден")
    
    elif intent == "edit_transaction":
        if not getattr(data, 'transaction_id', None):
            errors.append("Не указан номер записи")
    
    elif intent == "delete_transaction":
        if not getattr(data, 'transaction_id', None):
            errors.append("Не указан номер записи")
    
    return errors


async def handle_mutation_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle mutation intent (requires confirmation)."""
    intent = llm_response.intent
    data = llm_response.data
    
    # Validate required fields using shared function
    errors = validate_mutation_data(db, user, intent, data)
    
    # Cross-currency transfer check (async, only for transfers without errors)
    if intent == "transfer" and not errors:
        if data.from_account_name and data.to_account_name:
            from_acc = find_account_by_name(db, user.id, data.from_account_name)
            to_acc = find_account_by_name(db, user.id, data.to_account_name)
            
            if from_acc and to_acc and from_acc.currency != to_acc.currency:
                if not data.to_amount:
                    await update.message.reply_text(
                        f"⚠️ Кросс-валютный перевод!\n\n"
                        f"Счёт «{from_acc.name}» в {from_acc.currency}, "
                        f"а счёт «{to_acc.name}» в {to_acc.currency}.\n\n"
                        f"Укажи сумму зачисления, например:\n"
                        f"«перекинь с {from_acc.name} {int(data.amount)} {from_acc.currency} "
                        f"на {to_acc.name} XXX {to_acc.currency}»"
                    )
                    return
    
    if errors:
        await update.message.reply_text(
            f"Не хватает данных:\n" + "\n".join(f"• {e}" for e in errors) +
            "\n\nПопробуй указать все данные в сообщении."
        )
        return
    
    # Build preview message
    preview_text = build_preview_text(db, user, intent, data)
    
    # Create pending action
    payload = {
        "intent": intent,
        "data": data.model_dump(exclude_none=True)
    }
    
    expires_at = datetime.utcnow() + timedelta(minutes=15)
    pending = PendingAction(
        user_id=user.id,
        action_type=ActionType(intent),
        payload_json=payload,
        expires_at=expires_at,
        status=PendingStatus.PENDING
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    
    # Send preview with buttons
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"fin:confirm:{pending.id}"),
            InlineKeyboardButton("❌ Отменить", callback_data=f"fin:cancel:{pending.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    sent_message = await update.message.reply_text(preview_text, reply_markup=reply_markup)
    
    # Save preview message ID (non-critical, wrap in try-except)
    try:
        pending.preview_message_id = sent_message.message_id
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to save preview_message_id: {e}")
        # Not critical, the action is already saved


def build_preview_text(db: Session, user: User, intent: str, data) -> str:
    """Build preview text for pending action."""
    lines = []
    
    # Helper to build category string
    def format_category(cat, subcat):
        if cat and subcat:
            return f"  📂 {cat} → {subcat}"
        elif cat:
            return f"  📂 {cat}"
        return ""
    
    if intent == "income":
        amount = data.amount
        currency = data.currency or "RUB"
        account_name = data.account_name or "дефолтный счёт"
        date_str = format_operation_date(data.operation_date)
        desc = f"  📝 {data.description}" if data.description else ""
        cat_str = format_category(data.category, getattr(data, 'subcategory', None))
        
        lines.append(f"💰 Доход: {format_amount(Decimal(str(amount)), currency)}")
        lines.append(f"  📅 {date_str} • {account_name}")
        if cat_str:
            lines.append(cat_str)
        if desc:
            lines.append(desc)
    
    elif intent == "expense":
        amount = data.amount
        currency = data.currency or "RUB"
        account_name = data.account_name or "дефолтный счёт"
        date_str = format_operation_date(data.operation_date)
        desc = f"  📝 {data.description}" if data.description else ""
        cat_str = format_category(data.category, getattr(data, 'subcategory', None))
        
        lines.append(f"💸 Расход: {format_amount(Decimal(str(amount)), currency)}")
        lines.append(f"  📅 {date_str} • {account_name}")
        if cat_str:
            lines.append(cat_str)
        if desc:
            lines.append(desc)
    
    elif intent == "transfer":
        amount = data.amount
        currency = data.currency or "RUB"
        from_acc = data.from_account_name
        to_acc = data.to_account_name
        date_str = format_operation_date(data.operation_date)
        
        lines.append(f"🔄 Перевод ({date_str}):")
        lines.append(f"  {from_acc}: −{format_amount(Decimal(str(amount)), currency)}")
        
        # Cross-currency transfer
        if data.to_amount and data.to_currency:
            lines.append(f"  {to_acc}: +{format_amount(Decimal(str(data.to_amount)), data.to_currency)}")
        else:
            lines.append(f"  {to_acc}: +{format_amount(Decimal(str(amount)), currency)}")
    
    elif intent == "account_add":
        acc_new = data.account_new
        lines.append(f"💳 Создать счёт:")
        lines.append(f"  Название: {acc_new.name}")
        lines.append(f"  Валюта: {acc_new.currency}")
        if acc_new.initial_balance:
            lines.append(f"  Начальный баланс: {format_amount(Decimal(str(acc_new.initial_balance)), acc_new.currency)}")
    
    elif intent == "account_delete":
        lines.append(f"🗑️ Удалить счёт:")
        lines.append(f"  {data.account_name}")
    
    elif intent == "account_rename":
        lines.append(f"✏️ Переименовать счёт:")
        lines.append(f"  {data.account_old_name} → {data.account_new_name}")
    
    elif intent == "set_default_account":
        lines.append(f"⭐ Назначить дефолтным счётом:")
        lines.append(f"  {data.account_name}")
    
    elif intent == "clear_all_data":
        lines.append(f"⚠️ **УДАЛИТЬ ВСЕ ДАННЫЕ**")
        lines.append(f"")
        lines.append(f"Это действие **НЕОБРАТИМО** удалит:")
        lines.append(f"  • Все счета")
        lines.append(f"  • Все операции")
        lines.append(f"  • Всю историю")
    
    lines.append("\nПодтверди действие кнопками ниже.")
    
    return "\n".join(lines)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    
    if not query or not query.data or not query.data.startswith("fin:"):
        return
    
    # Don't answer here - each handler will answer
    
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    
    action = parts[1]
    db = get_db()
    
    try:
        if action == "confirm":
            pending_id = int(parts[2])
            await handle_confirm(db, query, pending_id)
        
        elif action == "cancel":
            pending_id = int(parts[2])
            await handle_cancel(db, query, pending_id)
        
        elif action == "insight":
            # Handle insight action buttons
            sub_action = parts[2]
            user_id = int(parts[3])
            await handle_insight_action(db, query, sub_action, user_id)
        
    except Exception as e:
        logger.error(f"Error in callback_handler: {e}", exc_info=True)
        await query.edit_message_text("Произошла ошибка.")
    finally:
        db.close()


def execute_single_operation(db: Session, user: User, intent: str, data_dict: dict):
    """Execute a single operation (used for both regular and batch operations)."""
    from utils.dates import get_user_timezone
    
    if intent == "income":
        amount = Decimal(str(data_dict["amount"]))
        account_name = data_dict.get("account_name")
        user_mentioned_currency = data_dict.get("currency")  # Currency from user's text
        
        if account_name:
            account = find_account_by_name(db, user.id, account_name)
        else:
            account = db.query(Account).filter(Account.id == user.default_account_id).first()
        
        if not account:
            raise ValueError("Счёт не найден")
        
        # Check currency mismatch
        if user_mentioned_currency and user_mentioned_currency.upper() != account.currency.upper():
            raise ValueError(
                f"Указана валюта {user_mentioned_currency.upper()}, но счёт «{account.name}» в {account.currency}.\n"
                f"Уточни счёт или измени валюту."
            )
        
        # Always use account currency
        currency = account.currency
        
        operation_date = None
        if data_dict.get("operation_date"):
            tz = get_user_timezone(user.timezone)
            operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
            if not operation_date.tzinfo:
                operation_date = tz.localize(operation_date)
        
        add_income(
            db,
            user.id,
            amount,
            currency,
            account.id,
            category=data_dict.get("category"),
            subcategory=data_dict.get("subcategory"),
            description=data_dict.get("description"),
            operation_date=operation_date
        )
    
    elif intent == "expense":
        amount = Decimal(str(data_dict["amount"]))
        account_name = data_dict.get("account_name")
        user_mentioned_currency = data_dict.get("currency")  # Currency from user's text
        
        if account_name:
            account = find_account_by_name(db, user.id, account_name)
        else:
            account = db.query(Account).filter(Account.id == user.default_account_id).first()
        
        if not account:
            raise ValueError("Счёт не найден")
        
        # Check currency mismatch
        if user_mentioned_currency and user_mentioned_currency.upper() != account.currency.upper():
            raise ValueError(
                f"Указана валюта {user_mentioned_currency.upper()}, но счёт «{account.name}» в {account.currency}.\n"
                f"Уточни счёт или измени валюту."
            )
        
        # Always use account currency
        currency = account.currency
        
        operation_date = None
        if data_dict.get("operation_date"):
            tz = get_user_timezone(user.timezone)
            operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
            if not operation_date.tzinfo:
                operation_date = tz.localize(operation_date)
        
        add_expense(
            db,
            user.id,
            amount,
            currency,
            account.id,
            category=data_dict.get("category"),
            subcategory=data_dict.get("subcategory"),
            description=data_dict.get("description"),
            operation_date=operation_date
        )
    
    elif intent == "transfer":
        amount = Decimal(str(data_dict["amount"]))
        currency = data_dict.get("currency") or "RUB"
        from_account = find_account_by_name(db, user.id, data_dict["from_account_name"])
        to_account = find_account_by_name(db, user.id, data_dict["to_account_name"])
        
        if not from_account or not to_account:
            raise ValueError("Один из счетов не найден")
        
        currency = currency or from_account.currency
        
        operation_date = None
        if data_dict.get("operation_date"):
            tz = get_user_timezone(user.timezone)
            operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
            if not operation_date.tzinfo:
                operation_date = tz.localize(operation_date)
        
        # Handle cross-currency transfers
        to_amount = None
        to_currency = None
        if data_dict.get("to_amount"):
            to_amount = Decimal(str(data_dict["to_amount"]))
            to_currency = data_dict.get("to_currency")
        
        transfer(
            db,
            user.id,
            amount,
            currency,
            from_account.id,
            to_account.id,
            to_amount=to_amount,
            to_currency=to_currency,
            description=data_dict.get("description"),
            operation_date=operation_date
        )
    
    elif intent == "account_add":
        acc_new = data_dict["account_new"]
        create_account(
            db,
            user.id,
            acc_new["name"],
            acc_new.get("currency", "RUB"),
            Decimal(str(acc_new.get("initial_balance", 0)))
        )
    
    elif intent == "account_delete":
        account = find_account_by_name(db, user.id, data_dict["account_name"])
        if not account:
            raise ValueError("Счёт не найден")
        delete_account(db, user.id, account.id)
    
    elif intent == "account_rename":
        account = find_account_by_name(db, user.id, data_dict["account_old_name"])
        if not account:
            raise ValueError("Счёт не найден")
        rename_account(db, user.id, account.id, data_dict["account_new_name"])
    
    elif intent == "set_default_account":
        account = find_account_by_name(db, user.id, data_dict["account_name"])
        if not account:
            raise ValueError("Счёт не найден")
        set_default_account(db, user.id, account.id)
    
    elif intent == "clear_all_data":
        from services.ledger import clear_user_data
        tx_deleted, acc_deleted = clear_user_data(db, user.id)
        logger.info(f"Cleared all data for user {user.id}: {acc_deleted} accounts, {tx_deleted} transactions")
    
    elif intent == "edit_transaction":
        tx_id = data_dict["transaction_id"]
        new_amount = Decimal(str(data_dict["new_amount"])) if data_dict.get("new_amount") else None
        new_category = data_dict.get("new_category")
        new_description = data_dict.get("new_description")
        
        update_transaction(
            db, user.id, tx_id,
            new_amount=new_amount,
            new_category=new_category,
            new_description=new_description
        )
    
    elif intent == "delete_transaction":
        tx_id = data_dict["transaction_id"]
        delete_transaction_by_id(db, user.id, tx_id)
    
    else:
        raise ValueError(f"Неизвестный intent: {intent}")


async def handle_confirm(db: Session, query, pending_id: int):
    """Handle confirmation callback."""
    pending = db.query(PendingAction).filter(PendingAction.id == pending_id).first()
    
    if not pending:
        await query.edit_message_text("Действие не найдено.")
        return
    
    # Check ownership - compare Telegram user ID
    user = db.query(User).filter(User.id == pending.user_id).first()
    if not user or user.tg_user_id != query.from_user.id:
        await query.answer("Нет доступа.", show_alert=True)
        return
    
    # Check expiration
    if datetime.utcnow() > pending.expires_at:
        pending.status = PendingStatus.EXPIRED
        db.commit()
        await query.edit_message_text("Время подтверждения истекло. Создай операцию заново сообщением.")
        return
    
    # Check status
    if pending.status != PendingStatus.PENDING:
        await query.edit_message_text("Действие уже обработано.")
        return
    
    # Execute action
    try:
        user = db.query(User).filter(User.id == pending.user_id).first()
        
        # Parse payload
        payload = json.loads(pending.payload_json) if isinstance(pending.payload_json, str) else pending.payload_json
        intent = payload.get("intent", "")
        
        # Check if this is sheets_import
        if intent == "sheets_import" or pending.action_type == ActionType.SHEETS_IMPORT:
            from services.ledger import clear_user_data, create_account, create_transaction_raw
            
            try:
                imported_data = payload.get("imported_data", {})
                accounts_data = imported_data.get("accounts", [])
                transactions_data = imported_data.get("transactions", [])
                
                # 1. Clear all existing user data
                tx_deleted, acc_deleted = clear_user_data(db, user.id)
                
                # 2. Create accounts with balances from Sheets
                account_map = {}  # name -> account_id
                accounts_created = 0
                first_account_id = None
                default_account_id = None
                
                for acc_dict in accounts_data:
                    try:
                        balance = Decimal(str(acc_dict.get("initial_balance", "0")))
                        account = create_account(
                            db, user.id, 
                            acc_dict["name"], 
                            acc_dict["currency"], 
                            initial_balance=balance
                        )
                        account_map[acc_dict["name"].lower()] = account.id
                        accounts_created += 1
                        if first_account_id is None:
                            first_account_id = account.id
                        if acc_dict.get("is_default"):
                            default_account_id = account.id
                    except Exception as e:
                        logger.error(f"Failed to create account {acc_dict['name']}: {e}")
                
                # Set default account if found in imported data
                if default_account_id:
                    user.default_account_id = default_account_id
                elif first_account_id:
                    # Fallback to first account if no default specified
                    user.default_account_id = first_account_id
                
                # 3. Create transactions WITHOUT updating balances
                transactions_created = 0
                for tx_dict in transactions_data:
                    try:
                        # Find account by name
                        account_id = account_map.get(tx_dict["account_name"].lower() if tx_dict.get("account_name") else None)
                        if not account_id:
                            # Use first account as fallback
                            account_id = first_account_id
                        
                        if account_id and tx_dict.get("operation_date"):
                            create_transaction_raw(
                                db=db,
                                user_id=user.id,
                                transaction_type=tx_dict["transaction_type"],
                                amount=Decimal(str(tx_dict["amount"])),
                                currency=tx_dict["currency"],
                                account_id=account_id,
                                category=tx_dict.get("category"),
                                description=tx_dict.get("description"),
                                operation_date=datetime.fromisoformat(tx_dict["operation_date"]),
                            )
                            transactions_created += 1
                    except Exception as e:
                        logger.error(f"Failed to create transaction: {e}")
                
                db.commit()
                pending.status = PendingStatus.CONFIRMED
                db.commit()
                
                result_text = f"""✅ Импорт завершён!

📊 Было удалено:
  • Счетов: {acc_deleted}
  • Операций: {tx_deleted}

📥 Импортировано из таблицы:
  • Счетов: {accounts_created}
  • Операций: {transactions_created}

💡 Балансы взяты из таблицы как есть."""
                
                await query.answer("✅ Импорт завершён!")
                await query.edit_message_text(result_text)
                return
            
            except Exception as e:
                db.rollback()
                logger.error(f"Sheets import error: {e}", exc_info=True)
                await query.edit_message_text(f"❌ Ошибка импорта: {str(e)}")
                return
        
        # Check if this is a batch operation
        if intent == "batch" or pending.action_type == ActionType.BATCH:
            operations = payload.get("operations", [])
            
            success_count = 0
            errors = []
            
            # Handle regular batch
            for i, op in enumerate(operations, 1):
                try:
                    execute_single_operation(db, user, op["intent"], op["data"])
                    success_count += 1
                except Exception as e:
                    errors.append(f"Операция {i}: {str(e)}")
            
            if errors:
                db.rollback()
                error_text = f"⚠️ Выполнено {success_count}/{len(operations)}.\nОшибки:\n" + "\n".join(errors)
                await query.edit_message_text(error_text)
            else:
                pending.status = PendingStatus.CONFIRMED
                db.commit()
                await query.answer(f"✅ Выполнено {success_count} операций.")
                await query.edit_message_text(f"✅ Выполнено {success_count} операций.")
            return
        
        # Regular single operation
        data_dict = payload["data"]
        
        if intent == "income":
            amount = Decimal(str(data_dict["amount"]))
            currency = data_dict.get("currency") or "RUB"
            account_name = data_dict.get("account_name")
            
            if account_name:
                account = find_account_by_name(db, user.id, account_name)
            else:
                account = db.query(Account).filter(Account.id == user.default_account_id).first()
            
            if not account:
                raise ValueError("Счёт не найден")
            
            currency = currency or account.currency
            
            operation_date = None
            if data_dict.get("operation_date"):
                from utils.dates import get_user_timezone
                tz = get_user_timezone(user.timezone)
                operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
                if not operation_date.tzinfo:
                    operation_date = tz.localize(operation_date)
            
            add_income(
                db,
                user.id,
                amount,
                currency,
                account.id,
                category=data_dict.get("category"),
                subcategory=data_dict.get("subcategory"),
                description=data_dict.get("description"),
                operation_date=operation_date
            )
        
        elif intent == "expense":
            amount = Decimal(str(data_dict["amount"]))
            account_name = data_dict.get("account_name")
            user_mentioned_currency = data_dict.get("currency")  # Currency from user's text
            
            if account_name:
                account = find_account_by_name(db, user.id, account_name)
            else:
                account = db.query(Account).filter(Account.id == user.default_account_id).first()
            
            if not account:
                raise ValueError("Счёт не найден")
            
            # Check currency mismatch
            if user_mentioned_currency and user_mentioned_currency.upper() != account.currency.upper():
                raise ValueError(
                    f"Указана валюта {user_mentioned_currency.upper()}, но счёт «{account.name}» в {account.currency}.\n"
                    f"Уточни счёт или измени валюту."
                )
            
            # Always use account currency
            currency = account.currency
            
            operation_date = None
            if data_dict.get("operation_date"):
                from utils.dates import get_user_timezone
                tz = get_user_timezone(user.timezone)
                operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
                if not operation_date.tzinfo:
                    operation_date = tz.localize(operation_date)
            
            add_expense(
                db,
                user.id,
                amount,
                currency,
                account.id,
                category=data_dict.get("category"),
                subcategory=data_dict.get("subcategory"),
                description=data_dict.get("description"),
                operation_date=operation_date
            )
        
        elif intent == "transfer":
            amount = Decimal(str(data_dict["amount"]))
            from_account = find_account_by_name(db, user.id, data_dict["from_account_name"])
            to_account = find_account_by_name(db, user.id, data_dict["to_account_name"])
            
            if not from_account or not to_account:
                raise ValueError("Один из счетов не найден")
            
            # Always use source account currency
            currency = from_account.currency
            
            operation_date = None
            if data_dict.get("operation_date"):
                from utils.dates import get_user_timezone
                tz = get_user_timezone(user.timezone)
                operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
                if not operation_date.tzinfo:
                    operation_date = tz.localize(operation_date)
            
            # Handle cross-currency transfers
            to_amount = None
            to_currency = None
            if data_dict.get("to_amount"):
                to_amount = Decimal(str(data_dict["to_amount"]))
                to_currency = data_dict.get("to_currency")
            
            transfer(
                db,
                user.id,
                amount,
                currency,
                from_account.id,
                to_account.id,
                to_amount=to_amount,
                to_currency=to_currency,
                description=data_dict.get("description"),
                operation_date=operation_date
            )
        
        elif intent == "account_add":
            acc_new = data_dict["account_new"]
            create_account(
                db,
                user.id,
                acc_new["name"],
                acc_new.get("currency", "RUB"),
                Decimal(str(acc_new.get("initial_balance", 0)))
            )
        
        elif intent == "account_delete":
            account = find_account_by_name(db, user.id, data_dict["account_name"])
            if not account:
                raise ValueError("Счёт не найден")
            delete_account(db, user.id, account.id)
        
        elif intent == "account_rename":
            account = find_account_by_name(db, user.id, data_dict["account_old_name"])
            if not account:
                raise ValueError("Счёт не найден")
            rename_account(db, user.id, account.id, data_dict["account_new_name"])
        
        elif intent == "set_default_account":
            account = find_account_by_name(db, user.id, data_dict["account_name"])
            if not account:
                raise ValueError("Счёт не найден")
            set_default_account(db, user.id, account.id)
        
        elif intent == "clear_all_data":
            from services.ledger import clear_user_data
            tx_deleted, acc_deleted = clear_user_data(db, user.id)
            logger.info(f"Cleared all data for user {user.id}: {acc_deleted} accounts, {tx_deleted} transactions")
        
        elif intent == "edit_transaction":
            tx_id = data_dict["transaction_id"]
            new_amount = Decimal(str(data_dict["new_amount"])) if data_dict.get("new_amount") else None
            new_category = data_dict.get("new_category")
            new_description = data_dict.get("new_description")
            
            update_transaction(
                db, user.id, tx_id,
                new_amount=new_amount,
                new_category=new_category,
                new_description=new_description
            )
        
        elif intent == "delete_transaction":
            tx_id = data_dict["transaction_id"]
            delete_transaction_by_id(db, user.id, tx_id)
        
        # Mark as confirmed
        pending.status = PendingStatus.CONFIRMED
        db.commit()
        
        # Answer callback to remove loading state
        await query.answer("✅ Подтверждено и записано.")
        await query.edit_message_text("✅ Подтверждено и записано.")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error executing action: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка при выполнении: {str(e)}")


async def handle_cancel(db: Session, query, pending_id: int):
    """Handle cancellation callback."""
    logger.info(f"handle_cancel called for pending_id={pending_id}")
    
    pending = db.query(PendingAction).filter(PendingAction.id == pending_id).first()
    
    if not pending:
        logger.warning(f"Pending action {pending_id} not found")
        await query.answer("Действие не найдено.", show_alert=True)
        await query.edit_message_text("❌ Действие не найдено.")
        return
    
    # Check ownership - compare Telegram user ID
    user = db.query(User).filter(User.id == pending.user_id).first()
    logger.info(f"User check: user_id={user.id if user else None}, tg_id={user.tg_user_id if user else None}, query_from={query.from_user.id}")
    
    if not user or user.tg_user_id != query.from_user.id:
        logger.warning(f"Access denied for user {query.from_user.id}")
        await query.answer("Нет доступа.", show_alert=True)
        return
    
    # Mark as cancelled
    pending.status = PendingStatus.CANCELLED
    db.commit()
    logger.info(f"Pending action {pending_id} marked as cancelled")
    
    # Answer callback to remove loading state
    await query.answer("❌ Отменено")
    
    # Edit message
    try:
        await query.edit_message_text("❌ Отменено. Напиши ещё раз, что ты хотел.")
        logger.info("Message edited successfully")
    except Exception as e:
        logger.error(f"Failed to edit message: {e}", exc_info=True)


async def handle_insight_action(db: Session, query, sub_action: str, user_id: int):
    """Handle insight action buttons."""
    # This is a simplified version - in production you'd store insight query params
    # For now, just show a message
    if sub_action == "top":
        await query.answer("Показываю топ операций...", show_alert=False)
        # In production, fetch and show top transactions
        await query.edit_message_text("Функция в разработке. Используй основной ответ выше.")
    elif sub_action == "byday":
        await query.answer("Показываю по дням...", show_alert=False)
        await query.edit_message_text("Функция в разработке. Используй основной ответ выше.")
    elif sub_action == "compare_prev_month":
        await query.answer("Сравниваю с прошлым месяцем...", show_alert=False)
        await query.edit_message_text("Функция в разработке. Используй основной ответ выше.")
    elif sub_action == "category":
        await query.answer("Уточняю категорию...", show_alert=False)
        await query.edit_message_text("Функция в разработке. Используй основной ответ выше.")

