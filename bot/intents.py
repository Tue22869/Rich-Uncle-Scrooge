"""Intent handlers: batch, report, accounts, transactions, insight, mutation."""
import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.orm import Session

from db.models import User, Account, PendingAction, ActionType, PendingStatus
from services.ledger import (
    find_account_by_name, list_user_transactions, get_transaction_by_row_number
)
from services.reports import get_report, format_report_text
from services.insights import get_insight, format_insight_text
from llm.parser import generate_analysis
from llm.prompts import format_insight_for_analysis
from utils.money import format_amount

from bot.helpers import (
    validate_mutation_data, build_preview_text, build_single_operation_preview
)

logger = logging.getLogger(__name__)


async def handle_batch_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response,
    accounts_list: list,
    default_account: str
):
    """Handle batch of multiple operations."""
    from schemas.llm_schema import LLMResponse

    operations = llm_response.operations or []

    if not operations:
        await update.message.reply_text("Не удалось распознать операции.")
        return

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

    for op in read_ops:
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

    if not mutation_ops:
        return

    accounts_to_create = set()
    for op in mutation_ops:
        if op.intent == "account_add":
            acc_new = getattr(op.data, 'account_new', None)
            if acc_new and getattr(acc_new, 'name', None):
                accounts_to_create.add(acc_new.name.lower())

    all_errors = []
    validated_ops = []

    for i, op in enumerate(mutation_ops, 1):
        errors = validate_mutation_data(db, user, op.intent, op.data)

        filtered_errors = []
        for error in errors:
            is_account_not_found = "не найден" in error.lower()
            if is_account_not_found:
                account_mentioned = False
                for acc_name in accounts_to_create:
                    if acc_name in error.lower():
                        account_mentioned = True
                        break
                if not account_mentioned:
                    filtered_errors.append(error)
            else:
                filtered_errors.append(error)

        if filtered_errors:
            all_errors.append(f"Операция {i}: " + ", ".join(filtered_errors))
        else:
            validated_ops.append(op)

    if all_errors:
        error_text = "⚠️ Ошибки в операциях:\n" + "\n".join(all_errors)
        await update.message.reply_text(error_text)
        if not validated_ops:
            return

    preview_lines = ["📋 *Несколько операций:*\n"]

    for i, op in enumerate(validated_ops, 1):
        preview_line = build_single_operation_preview(op.intent, op.data, user.timezone)
        preview_lines.append(f"{i}. {preview_line}")

    preview_lines.append("\nПодтверди все операции кнопками ниже.")
    preview_text = "\n".join(preview_lines)

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

    pending.preview_message_id = sent_message.message_id
    db.commit()


async def handle_report_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response,
    original_text: str = ""
):
    """Handle report intent — shows static report + optional GPT analysis button."""
    data = llm_response.data
    period = data.period

    try:
        from services.analytics import log_event, log_first_feature
        from db.models import UsageEventKind
        log_event(db, user_id=user.id, kind=UsageEventKind.REPORT_VIEW,
                  meta={"preset": period.preset if period else None})
        log_first_feature(db, user.id, "report")
    except Exception:
        pass

    report = get_report(
        db,
        user.id,
        period_preset=period.preset if period else None,
        from_date=period.from_date if period else None,
        to_date=period.to if period else None,
        user_timezone=user.timezone
    )

    text = format_report_text(report, user.timezone)

    if period and period.preset and period.preset != "custom":
        period_str = period.preset
    elif period and period.from_date and period.to:
        period_str = f"{period.from_date},{period.to}"
    else:
        period_str = "month"

    keyboard = [[
        InlineKeyboardButton(
            "🤖 Анализ от GPT",
            callback_data=f"fin:report_analysis:{user.tg_user_id}:{period_str}"
        )
    ]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


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

    default_account_id = user.default_account_id
    if not default_account_id:
        for acc in accounts:
            if acc.is_default:
                default_account_id = acc.id
                break
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

    from_date = None
    to_date = None
    if period:
        if period.from_date:
            try:
                from_date = date.fromisoformat(period.from_date)
            except:
                pass
        if period.to:
            try:
                to_date = date.fromisoformat(period.to)
            except:
                pass

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
        if tx.type.value == "income":
            emoji = "💰"
            sign = "+"
        elif tx.type.value == "expense":
            emoji = "💸"
            sign = "-"
        else:
            emoji = "🔄"
            sign = ""

        date_str = tx.operation_date.strftime("%d.%m") if tx.operation_date else ""

        cat_parts = []
        if tx.category:
            cat_parts.append(tx.category)
        if tx.subcategory:
            cat_parts.append(tx.subcategory)
        if tx.description:
            cat_parts.append(tx.description)
        desc = " — " + " / ".join(cat_parts) if cat_parts else ""

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

    tx = get_transaction_by_row_number(db, user.id, data.transaction_id)

    if not tx:
        await update.message.reply_text(f"❌ Запись #{data.transaction_id} не найдена. Посмотри список: \"история операций\"")
        return

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

    tx = get_transaction_by_row_number(db, user.id, data.transaction_id)

    if not tx:
        await update.message.reply_text(f"❌ Запись #{data.transaction_id} не найдена. Посмотри список: \"история операций\"")
        return

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
    llm_response,
    original_text: str = ""
):
    """Handle insight intent (read-only, with action buttons)."""
    data = llm_response.data
    insight_query = data.insight_query

    if not insight_query:
        metric = data.metric
        if not metric:
            await update.message.reply_text("Не удалось понять вопрос. Попробуй переформулировать.")
            return

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

    data_str = format_insight_for_analysis(insight)
    # Engagement event before the LLM call so we can count "wanted analysis"
    # separately from "got analysis" (denominator for analysis success rate).
    try:
        from services.analytics import log_event
        from db.models import UsageEventKind
        log_event(db, user_id=user.id, kind=UsageEventKind.ANALYSIS_VIEW,
                  meta={"source": "insight"})
    except Exception:
        pass

    analysis = await generate_analysis(
        data_str, user_question=original_text, db=db, user_id=user.id
    )
    text = analysis if analysis else format_insight_text(insight, user.timezone)

    await update.message.reply_text(text)


async def handle_mutation_intent(
    db: Session,
    update: Update,
    user: User,
    llm_response
):
    """Handle mutation intent (requires confirmation)."""
    intent = llm_response.intent
    data = llm_response.data

    errors = validate_mutation_data(db, user, intent, data)

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
            "Не хватает данных:\n" + "\n".join(f"• {e}" for e in errors) +
            "\n\nПопробуй указать все данные в сообщении."
        )
        return

    preview_text = build_preview_text(db, user, intent, data)

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

    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"fin:confirm:{pending.id}"),
            InlineKeyboardButton("❌ Отменить", callback_data=f"fin:cancel:{pending.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    sent_message = await update.message.reply_text(preview_text, reply_markup=reply_markup)

    try:
        pending.preview_message_id = sent_message.message_id
        db.commit()
    except Exception as e:
        logger.warning(f"Failed to save preview_message_id: {e}")
