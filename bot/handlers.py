"""Bot handlers — entry points for Telegram commands and messages."""
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
from services.ledger import get_or_create_user, find_account_by_name
from services.reports import get_report, format_report_text
from llm.parser import parse_message
from utils.money import format_amount

from bot.helpers import get_db
from bot.intents import (
    handle_batch_intent, handle_report_intent, handle_show_accounts_intent,
    handle_list_transactions_intent, handle_edit_transaction_intent,
    handle_delete_transaction_intent, handle_insight_intent, handle_mutation_intent
)
from bot.callbacks import (
    handle_confirm, handle_cancel, handle_undo, handle_report_analysis_callback
)

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
        
        report = get_report(db, user.id, period_preset="month", user_timezone=user.timezone)
        text = format_report_text(report, user.timezone)
        keyboard = [[InlineKeyboardButton(
            "🤖 Анализ от GPT",
            callback_data=f"fin:report_analysis:{user.tg_user_id}:month"
        )]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Error in report_command: {e}")
        await update.message.reply_text("Произошла ошибка при формировании отчёта.")
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
        
        pending = db.query(PendingAction).filter(
            PendingAction.user_id == user.id,
            PendingAction.status == PendingStatus.PENDING,
            PendingAction.expires_at > datetime.utcnow()
        ).first()
        
        if pending:
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
    
    await process_user_text(update, context, update.message.text)


async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages - transcribe and process as text."""
    from services.speech import transcribe_telegram_voice
    
    voice = update.message.voice
    if not voice:
        return
    
    processing_msg = await update.message.reply_text("🎤 Распознаю голосовое сообщение...")
    
    try:
        text = await transcribe_telegram_voice(context.bot, voice.file_id)
        
        if not text or not text.strip():
            await processing_msg.edit_text("❌ Не удалось распознать речь. Попробуй ещё раз или напиши текстом.")
            return
        
        text = text.strip()
        
        await processing_msg.edit_text(f"🎤 Распознано: _{text}_", parse_mode="Markdown")
        
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
        
        user = get_or_create_user(db, user_id)
        
        # Check if user has pending clarification
        pending_clarification = db.query(PendingAction).filter(
            PendingAction.user_id == user.id,
            PendingAction.action_type == ActionType.CLARIFICATION,
            PendingAction.status == PendingStatus.PENDING,
            PendingAction.expires_at > datetime.utcnow()
        ).order_by(PendingAction.created_at.desc()).first()
        
        if pending_clarification:
            payload = json.loads(pending_clarification.payload_json)
            original_message = payload.get("original_message", "")
            
            answer_lower = text.lower().strip()
            matching_account = None
            for acc in db.query(Account).filter(Account.user_id == user.id).all():
                if acc.name.lower() in answer_lower or answer_lower in acc.name.lower():
                    matching_account = acc
                    break
            
            if matching_account:
                combined_message = f"{original_message} со счёта {matching_account.name}"
            else:
                combined_message = f"{original_message}. {text}"
            
            pending_clarification.status = PendingStatus.CONFIRMED
            db.commit()
            
            text = combined_message
        
        accounts_list = db.query(Account).filter(Account.user_id == user.id).all()
        
        # SYNC default account BEFORE parsing
        default_account = None
        
        if user.default_account_id:
            default_account = db.query(Account).filter(Account.id == user.default_account_id).first()
        
        if not default_account:
            default_account = db.query(Account).filter(
                Account.user_id == user.id,
                Account.is_default == True
            ).first()
            
            if default_account:
                user.default_account_id = default_account.id
                db.commit()
                logger.info(f"Synced default_account_id={default_account.id} for user {user.id}")
        
        if not default_account and len(accounts_list) == 1:
            default_account = accounts_list[0]
            user.default_account_id = default_account.id
            default_account.is_default = True
            db.commit()
            logger.info(f"Auto-set default account {default_account.name} for user {user.id}")
        
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
        
        if llm_response.intent == "batch":
            await handle_batch_intent(db, update, user, llm_response, accounts_list, default_account)
            message_sent = True
            return
        
        if llm_response.intent == "report":
            await handle_report_intent(db, update, user, llm_response, original_text=text)
            message_sent = True
            return
        
        if llm_response.intent == "show_accounts":
            await handle_show_accounts_intent(db, update, user)
            message_sent = True
            return
        
        if llm_response.intent == "insight":
            await handle_insight_intent(db, update, user, llm_response, original_text=text)
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


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks."""
    query = update.callback_query
    
    if not query or not query.data or not query.data.startswith("fin:"):
        return
    
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

        elif action == "undo":
            pending_id = int(parts[2])
            await handle_undo(db, query, pending_id)

        elif action == "report_analysis":
            if len(parts) >= 4:
                await handle_report_analysis_callback(db, query, parts[2], parts[3])

        
    except Exception as e:
        logger.error(f"Error in callback_handler: {e}", exc_info=True)
        await query.edit_message_text("Произошла ошибка.")
    finally:
        db.close()
