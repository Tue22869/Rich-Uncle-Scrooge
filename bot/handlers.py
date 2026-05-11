"""Bot handlers — entry points for Telegram commands and messages."""
import asyncio
import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta


from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes
from sqlalchemy.orm import Session

from db.models import User, Account, PendingAction, ActionType, PendingStatus, Budget
from services.ledger import get_or_create_user
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

# Reusable inline button row for "Главное меню"
MENU_BUTTON = [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")]

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
    """Handle /start command — onboarding with buttons."""
    logger.info(f"start_command called by user {update.effective_user.id}")
    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)

        accounts = db.query(Account).filter(Account.user_id == user.id).all()

        if not accounts:
            # New user onboarding — button-based account creation
            buttons = [
                [
                    InlineKeyboardButton("💵 Наличные (RUB)", callback_data="acct:create:Наличные:RUB"),
                    InlineKeyboardButton("💳 Карта (RUB)", callback_data="acct:create:Карта:RUB"),
                ],
                [
                    InlineKeyboardButton("💲 Карта (USD)", callback_data="acct:create:Карта USD:USD"),
                    InlineKeyboardButton("💶 Карта (EUR)", callback_data="acct:create:Карта EUR:EUR"),
                ],
                [InlineKeyboardButton("✏️ Создать свой счёт", callback_data="acct:custom")],
            ]

            # Offer trial if not used
            if not user.trial_used:
                buttons.append([
                    InlineKeyboardButton("🎁 Пробный период (14 дней)", callback_data="sub:activate_trial"),
                ])

            buttons.append([InlineKeyboardButton("❓ Помощь", callback_data="menu:help")])

            await update.message.reply_text(
                "💰 *Дядя Скрудж к вашим услугам!*\n\n"
                "Буду считать твои деньги и следить, чтобы ни одна монетка не пропала. 🦆\n\n"
                "Для начала создай свой первый счёт:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown",
            )
        else:
            # Returning user — show main menu
            from bot.menu import _show_main_menu
            # Send a welcome-back text, then show menu
            accounts_text = "\n".join([
                f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}"
                for acc in accounts
            ])
            await update.message.reply_text(
                f"💰 С возвращением! Твои счета:\n{accounts_text}"
            )
            await _show_main_menu(update)
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

        # Check subscription
        from bot.middleware import check_subscription
        allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
        if not allowed:
            await update.message.reply_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
            return

        accounts = db.query(Account).filter(Account.user_id == user.id).all()
        
        if not accounts:
            await update.message.reply_text(
                "💰 Пока пусто. Создай первый счёт!",
                reply_markup=InlineKeyboardMarkup([MENU_BUTTON]),
            )
        else:
            lines = ["💰 Твои счета:\n"]
            for acc in accounts:
                default_mark = " ⭐" if acc.is_default else ""
                lines.append(
                    f"  • {acc.name} ({acc.currency}): {format_amount(acc.balance, acc.currency)}{default_mark}"
                )
            await update.message.reply_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup([MENU_BUTTON]),
            )
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

        # Check subscription
        from bot.middleware import check_subscription
        allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
        if not allowed:
            await update.message.reply_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
            return

        report = get_report(db, user.id, period_preset="month", user_timezone=user.timezone)
        text = format_report_text(report, user.timezone)
        keyboard = [
            [InlineKeyboardButton(
                "🤖 Анализ от GPT",
                callback_data=f"fin:report_analysis:{user.tg_user_id}:month"
            )],
            MENU_BUTTON,
        ]
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

*💎 Подписка*

Все возможности бота доступны по подписке Premium.
• 🎁 Пробный период: 14 дней бесплатно (один раз)
• 📅 Месяц: 190₽
• 📆 Год: 1900₽ (выгода ~17%)

Управление: ⚙️ Настройки → 💎 Подписка

⸻

*📋 Бюджеты*
• бюджет на кофе 3000
• лимит на еду 15000₽ в месяц

_Бот предупредит при 80% и 100% расхода бюджета._

⸻

*🔥 Стрики и достижения*

Записывай расходы каждый день — бот считает стрик!
Открывай ачивки: первая операция, 7 дней подряд, 100 операций и другие.

⸻

✅ _Категории определяются автоматически. Все операции требуют подтверждения кнопкой._"""
    await update.message.reply_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([MENU_BUTTON]),
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages."""
    if not update.message or not update.message.text:
        return

    # Check persistent menu buttons first
    from bot.menu import handle_persistent_menu
    if await handle_persistent_menu(update, context):
        return

    # Check if user is in custom account creation flow (name or balance step)
    if context.user_data.get("custom_account_currency") or context.user_data.get("custom_account_name"):
        from bot.account_setup import handle_custom_account_name
        await handle_custom_account_name(update, context)
        return

    db = get_db()
    try:
        user = get_or_create_user(db, update.effective_user.id)

        # Lightweight engagement event — never blocks the flow.
        try:
            from services.analytics import log_event
            from db.models import UsageEventKind
            log_event(db, user_id=user.id, kind=UsageEventKind.MESSAGE_IN,
                      meta={"is_voice": False})
        except Exception:
            pass

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
        except Exception:
            pass

    await process_user_text(update, context, update.message.text)


async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages - transcribe and process as text."""
    from services.speech import transcribe_telegram_voice
    from bot.middleware import check_subscription
    from services.analytics import log_event
    from db.models import UsageEventKind

    voice = update.message.voice
    if not voice:
        return

    # Check subscription
    db = get_db()
    user_id = None
    try:
        user = get_or_create_user(db, update.effective_user.id)
        user_id = user.id
        log_event(db, user_id=user_id, kind=UsageEventKind.VOICE_IN,
                  meta={"duration_telegram_s": getattr(voice, "duration", None)})
        allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
        if not allowed:
            await update.message.reply_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
            return
    finally:
        db.close()

    processing_msg = await update.message.reply_text("🎤 Распознаю голосовое сообщение...")

    try:
        analytics_db = get_db()
        try:
            text = await transcribe_telegram_voice(
                context.bot, voice.file_id, db=analytics_db, user_id=user_id
            )
        finally:
            analytics_db.close()
        
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

        # Check subscription before processing
        from bot.middleware import check_subscription
        allowed, paywall_text, paywall_keyboard = await check_subscription(db, user)
        if not allowed:
            await update.message.reply_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
            message_sent = True
            return
        
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
            user.timezone,
            db=db,
            user_id=user.id,
        )
        
        logger.info(f"Parsed intent: {llm_response.intent}, confidence: {llm_response.confidence}")
        
        if llm_response.confidence < 0.5:
            await update.message.reply_text(
                "Не понял. Попробуй написать по-другому или используй /help для примеров.",
                reply_markup=InlineKeyboardMarkup([MENU_BUTTON]),
            )
            message_sent = True
            return
        
        if llm_response.intent == "unknown":
            await update.message.reply_text(
                "Не понял. Попробуй написать по-другому или используй /help для примеров.",
                reply_markup=InlineKeyboardMarkup([MENU_BUTTON]),
            )
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

        if llm_response.intent == "set_budget":
            await _handle_set_budget(db, update, user, llm_response)
            message_sent = True
            return

        await handle_mutation_intent(db, update, user, llm_response)
        message_sent = True
        
    except Exception as e:
        logger.error(f"Error in process_user_text: {e}", exc_info=True)
        if not message_sent:
            try:
                await update.message.reply_text(
                    "Произошла ошибка. Попробуй позже.",
                    reply_markup=InlineKeyboardMarkup([MENU_BUTTON]),
                )
            except:
                pass
    finally:
        try:
            db.close()
        except:
            pass


async def _handle_set_budget(db: Session, update: Update, user: User, llm_response):
    """Handle set_budget intent — create or update a budget for a category."""

    category = llm_response.data.budget_category
    limit_val = llm_response.data.budget_limit

    if not category or not limit_val or limit_val <= 0:
        await update.message.reply_text(
            "Не понял бюджет. Напиши, например:\n"
            "«бюджет на кофе 3000» или «лимит на еду 15000₽»"
        )
        return

    limit_decimal = Decimal(str(limit_val))

    # Upsert: update existing or create new
    existing = (
        db.query(Budget)
        .filter(Budget.user_id == user.id, Budget.category == category)
        .first()
    )

    if existing:
        old_limit = existing.monthly_limit
        existing.monthly_limit = limit_decimal
        db.commit()
        await update.message.reply_text(
            f"📝 Бюджет «{category}» обновлён:\n"
            f"  Было: {format_amount(old_limit, 'RUB')}/мес\n"
            f"  Стало: {format_amount(limit_decimal, 'RUB')}/мес\n\n"
            f"Буду предупреждать при 80% и 100% расхода.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
            ]),
        )
    else:
        budget = Budget(
            user_id=user.id,
            category=category,
            monthly_limit=limit_decimal,
            currency="RUB",
        )
        db.add(budget)
        db.commit()
        await update.message.reply_text(
            f"✅ Бюджет установлен:\n"
            f"  📂 {category}: {format_amount(limit_decimal, 'RUB')}/мес\n\n"
            f"Буду предупреждать при 80% и 100% расхода.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Главное меню", callback_data="menu:main")],
            ]),
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button callbacks — routes by prefix."""
    query = update.callback_query

    if not query or not query.data:
        return

    data = query.data

    # Route by prefix
    if data.startswith("menu:") or data.startswith("cmd:") or data.startswith("report:") or data.startswith("settings:"):
        from bot.menu import menu_callback_handler
        await menu_callback_handler(update, context)
        return

    if data.startswith("acct:"):
        from bot.account_setup import account_setup_callback, account_action_callback, _handle_balance_skip
        if data in ("acct:more", "acct:back_to_start"):
            await account_action_callback(update, context)
        elif data.startswith("acct:balance:"):
            await _handle_balance_skip(update, context)
        else:
            await account_setup_callback(update, context)
        return

    if data.startswith("sub:"):
        from bot.subscription import subscription_callback_handler
        await subscription_callback_handler(update, context)
        return

    if not data.startswith("fin:"):
        return

    parts = data.split(":")
    if len(parts) < 3:
        return

    action = parts[1]
    db = get_db()

    try:
        # Check subscription for confirm/undo/report_analysis (require active sub)
        if action in ("confirm", "undo", "report_analysis"):
            from bot.middleware import check_subscription as _check_sub
            user = db.query(User).filter(User.tg_user_id == query.from_user.id).first()
            if user:
                allowed, paywall_text, paywall_keyboard = await _check_sub(db, user)
                if not allowed:
                    await query.edit_message_text(paywall_text, reply_markup=paywall_keyboard, parse_mode="Markdown")
                    return

        if action == "confirm":
            pending_id = int(parts[2])
            await handle_confirm(db, query, pending_id)

            # Update streak and check budget alerts after successful confirmation
            try:
                from services.retention import update_streak, format_achievement_notification, check_budget_alerts
                user = db.query(User).filter(User.tg_user_id == query.from_user.id).first()
                if user:
                    result = update_streak(db, user)
                    if result["new_achievements"]:
                        notif = format_achievement_notification(result["new_achievements"])
                        await query.message.reply_text(notif, parse_mode="Markdown")
                    # Check budget alerts
                    alerts = check_budget_alerts(db, user)
                    for alert in alerts:
                        await query.message.reply_text(alert, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Streak/budget update failed: {e}")

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


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate a Telegram Stars invoice payload before charging the user."""
    from services.billing import parse_stars_invoice_payload

    query = update.pre_checkout_query
    parsed = parse_stars_invoice_payload(query.invoice_payload)
    if parsed is None:
        logger.error(f"Rejecting pre_checkout: bad payload {query.invoice_payload!r}")
        await query.answer(ok=False, error_message="Внутренняя ошибка. Свяжитесь с поддержкой.")
        return

    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate subscription after a successful Telegram Stars payment."""
    from services.billing import confirm_stars_payment

    payment = update.message.successful_payment
    if not payment:
        return

    if payment.currency != "XTR":
        # We only sell via Stars right now; YooKassa flows go through the webhook.
        logger.warning(f"Received non-Stars successful_payment currency={payment.currency}")
        return

    db = get_db()
    try:
        sub = confirm_stars_payment(
            db,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            invoice_payload=payment.invoice_payload,
            total_amount=payment.total_amount,
        )
    finally:
        db.close()

    if not sub:
        await update.message.reply_text(
            "⚠️ Платёж прошёл, но активация подписки не удалась. "
            "Напишите в поддержку — мы восстановим вручную.",
        )
        return

    expires = sub.expires_at.strftime("%d.%m.%Y")
    plan_labels = {"monthly": "Месяц", "yearly": "Год"}
    plan_label = plan_labels.get(sub.plan.value, sub.plan.value)
    await update.message.reply_text(
        f"🎉 *Подписка оформлена!*\n\n"
        f"📋 Тариф: {plan_label}\n"
        f"📅 Действует до: {expires}\n\n"
        "✨ Все возможности бота теперь доступны!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
        ]),
        parse_mode="Markdown",
    )
