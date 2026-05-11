"""Callback query handlers: confirm, cancel, undo, report analysis."""
import json
import logging
from decimal import Decimal
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.orm import Session

from db.models import User, Account, PendingAction, ActionType, PendingStatus

# Reusable menu button
_MENU_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")]])
from services.ledger import (
    find_account_by_name, add_income, add_expense,
    transfer, delete_account, rename_account,
    set_default_account, update_transaction, delete_transaction_by_id
)
from services.reports import get_report
from llm.parser import generate_analysis
from llm.prompts import format_report_for_analysis

from bot.helpers import execute_single_operation, _build_confirmed_text

logger = logging.getLogger(__name__)


async def handle_confirm(db: Session, query, pending_id: int):
    """Handle confirmation callback."""
    pending = db.query(PendingAction).filter(PendingAction.id == pending_id).first()

    if not pending:
        await query.edit_message_text("Действие не найдено.")
        return

    user = db.query(User).filter(User.id == pending.user_id).first()
    if not user or user.tg_user_id != query.from_user.id:
        await query.answer("Нет доступа.", show_alert=True)
        return

    if datetime.utcnow() > pending.expires_at:
        pending.status = PendingStatus.EXPIRED
        db.commit()
        await query.edit_message_text("Время подтверждения истекло. Создай операцию заново сообщением.")
        return

    if pending.status != PendingStatus.PENDING:
        await query.edit_message_text("Действие уже обработано.")
        return

    try:
        user = db.query(User).filter(User.id == pending.user_id).first()

        payload = json.loads(pending.payload_json) if isinstance(pending.payload_json, str) else pending.payload_json
        intent = payload.get("intent", "")

        # --- Sheets import ---
        if intent == "sheets_import" or pending.action_type == ActionType.SHEETS_IMPORT:
            from services.ledger import clear_user_data, create_account, create_transaction_raw

            try:
                imported_data = payload.get("imported_data", {})
                accounts_data = imported_data.get("accounts", [])
                transactions_data = imported_data.get("transactions", [])

                tx_deleted, acc_deleted = clear_user_data(db, user.id)

                account_map = {}
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

                if default_account_id:
                    user.default_account_id = default_account_id
                elif first_account_id:
                    user.default_account_id = first_account_id

                transactions_created = 0
                for tx_dict in transactions_data:
                    try:
                        account_id = account_map.get(tx_dict["account_name"].lower() if tx_dict.get("account_name") else None)
                        if not account_id:
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

        # --- Batch operations ---
        if intent == "batch" or pending.action_type == ActionType.BATCH:
            operations = payload.get("operations", [])

            success_count = 0
            errors = []
            undo_tx_ids = []

            for i, op in enumerate(operations, 1):
                try:
                    result = execute_single_operation(db, user, op["intent"], op["data"])
                    success_count += 1
                    if result and hasattr(result, 'id') and op["intent"] in ("income", "expense", "transfer"):
                        undo_tx_ids.append(result.id)
                except Exception as e:
                    errors.append(f"Операция {i}: {str(e)}")

            if errors:
                db.rollback()
                error_text = f"⚠️ Выполнено {success_count}/{len(operations)}.\nОшибки:\n" + "\n".join(errors)
                await query.edit_message_text(error_text)
            else:
                pending.status = PendingStatus.CONFIRMED
                if undo_tx_ids:
                    payload["undo_data"] = {"tx_ids": undo_tx_ids}
                    pending.payload_json = payload
                db.commit()

                confirmed_text = _build_confirmed_text(query.message.text)
                if undo_tx_ids:
                    keyboard = [
                        [InlineKeyboardButton("↩️ Отменить", callback_data=f"fin:undo:{pending.id}")],
                        [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
                    ]
                    await query.answer(f"✅ Выполнено {success_count} операций.")
                    await query.edit_message_text(confirmed_text, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await query.answer(f"✅ Выполнено {success_count} операций.")
                    await query.edit_message_text(confirmed_text, reply_markup=_MENU_KB)
            return

        # --- Regular single operation ---
        data_dict = payload["data"]
        undo_tx_ids = []
        undo_account_id = None

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

            tx = add_income(
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
            undo_tx_ids = [tx.id]

        elif intent == "expense":
            amount = Decimal(str(data_dict["amount"]))
            account_name = data_dict.get("account_name")
            user_mentioned_currency = data_dict.get("currency")

            if account_name:
                account = find_account_by_name(db, user.id, account_name)
            else:
                account = db.query(Account).filter(Account.id == user.default_account_id).first()

            if not account:
                raise ValueError("Счёт не найден")

            if user_mentioned_currency and user_mentioned_currency.upper() != account.currency.upper():
                raise ValueError(
                    f"Указана валюта {user_mentioned_currency.upper()}, но счёт «{account.name}» в {account.currency}.\n"
                    f"Уточни счёт или измени валюту."
                )

            currency = account.currency

            operation_date = None
            if data_dict.get("operation_date"):
                from utils.dates import get_user_timezone
                tz = get_user_timezone(user.timezone)
                operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
                if not operation_date.tzinfo:
                    operation_date = tz.localize(operation_date)

            tx = add_expense(
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
            undo_tx_ids = [tx.id]

        elif intent == "transfer":
            amount = Decimal(str(data_dict["amount"]))
            from_account = find_account_by_name(db, user.id, data_dict["from_account_name"])
            to_account = find_account_by_name(db, user.id, data_dict["to_account_name"])

            if not from_account or not to_account:
                raise ValueError("Один из счетов не найден")

            currency = from_account.currency

            operation_date = None
            if data_dict.get("operation_date"):
                from utils.dates import get_user_timezone
                tz = get_user_timezone(user.timezone)
                operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
                if not operation_date.tzinfo:
                    operation_date = tz.localize(operation_date)

            to_amount = None
            to_currency = None
            if data_dict.get("to_amount"):
                to_amount = Decimal(str(data_dict["to_amount"]))
                to_currency = data_dict.get("to_currency")

            tx = transfer(
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
            undo_tx_ids = [tx.id]

        elif intent == "account_add":
            acc_new = data_dict["account_new"]
            new_acc = create_account(
                db,
                user.id,
                acc_new["name"],
                acc_new.get("currency", "RUB"),
                Decimal(str(acc_new.get("initial_balance", 0)))
            )
            undo_account_id = new_acc.id

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

        # Mark as confirmed + store undo data
        pending.status = PendingStatus.CONFIRMED
        if undo_tx_ids or undo_account_id:
            payload["undo_data"] = {"tx_ids": undo_tx_ids, "account_id": undo_account_id}
            pending.payload_json = payload
        db.commit()

        confirmed_text = _build_confirmed_text(query.message.text)

        undoable = {"income", "expense", "transfer", "account_add"}
        if intent in undoable and (undo_tx_ids or undo_account_id):
            keyboard = [
                [InlineKeyboardButton("↩️ Отменить", callback_data=f"fin:undo:{pending.id}")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
            ]
            await query.answer("✅ Записано")
            await query.edit_message_text(confirmed_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.answer("✅ Подтверждено и записано.")
            await query.edit_message_text(confirmed_text, reply_markup=_MENU_KB)

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

    user = db.query(User).filter(User.id == pending.user_id).first()
    logger.info(f"User check: user_id={user.id if user else None}, tg_id={user.tg_user_id if user else None}, query_from={query.from_user.id}")

    if not user or user.tg_user_id != query.from_user.id:
        logger.warning(f"Access denied for user {query.from_user.id}")
        await query.answer("Нет доступа.", show_alert=True)
        return

    pending.status = PendingStatus.CANCELLED
    db.commit()
    logger.info(f"Pending action {pending_id} marked as cancelled")

    await query.answer("❌ Отменено")

    try:
        await query.edit_message_text("❌ Отменено. Напиши ещё раз, что ты хотел.", reply_markup=_MENU_KB)
        logger.info("Message edited successfully")
    except Exception as e:
        logger.error(f"Failed to edit message: {e}", exc_info=True)


async def handle_undo(db: Session, query, pending_id: int):
    """Reverse a previously confirmed operation."""
    pending = db.query(PendingAction).filter(PendingAction.id == pending_id).first()

    if not pending:
        await query.answer("Действие не найдено.", show_alert=True)
        return

    user = db.query(User).filter(User.id == pending.user_id).first()
    if not user or user.tg_user_id != query.from_user.id:
        await query.answer("Нет доступа.", show_alert=True)
        return

    if pending.status != PendingStatus.CONFIRMED:
        await query.edit_message_text("Операция уже отменена или недоступна.")
        return

    # Only allow undo for the user's most recent confirmed operation
    newer_confirmed = db.query(PendingAction).filter(
        PendingAction.user_id == pending.user_id,
        PendingAction.id > pending_id,
        PendingAction.status == PendingStatus.CONFIRMED
    ).first()
    if newer_confirmed:
        await query.answer("↩️ Отмена доступна только для последней операции.", show_alert=True)
        return

    payload = json.loads(pending.payload_json) if isinstance(pending.payload_json, str) else pending.payload_json
    undo_data = payload.get("undo_data")

    if not undo_data:
        await query.answer("Отмена недоступна для этой операции.", show_alert=True)
        return

    try:
        tx_ids = undo_data.get("tx_ids", [])
        account_id = undo_data.get("account_id")

        for tx_id in tx_ids:
            delete_transaction_by_id(db, user.id, tx_id)

        if account_id:
            delete_account(db, user.id, account_id)

        pending.status = PendingStatus.CANCELLED
        db.commit()

        await query.answer("↩️ Отменено")
        await query.edit_message_text("↩️ Операция отменена.", reply_markup=_MENU_KB)
    except Exception as e:
        db.rollback()
        logger.error(f"Undo error: {e}", exc_info=True)
        await query.answer(f"Ошибка: {str(e)}", show_alert=True)


async def handle_report_analysis_callback(db: Session, query, user_id_str: str, period_str: str):
    """Handle 'Анализ от GPT' button on a report — re-fetch data and run LLM analysis."""
    await query.answer("🤖 Анализирую...")

    try:
        user = db.query(User).filter(User.tg_user_id == int(user_id_str)).first()
        if not user or user.tg_user_id != query.from_user.id:
            await query.answer("Нет доступа.", show_alert=True)
            return

        if "," in period_str:
            from_date, to_date = period_str.split(",", 1)
            preset = None
        else:
            from_date, to_date, preset = None, None, period_str

        report = get_report(
            db,
            user.id,
            period_preset=preset,
            from_date=from_date,
            to_date=to_date,
            user_timezone=user.timezone
        )

        data_str = format_report_for_analysis(report)
        try:
            from services.analytics import log_event
            from db.models import UsageEventKind
            log_event(db, user_id=user.id, kind=UsageEventKind.ANALYSIS_VIEW,
                      meta={"source": "report"})
        except Exception:
            pass

        analysis = await generate_analysis(data_str, db=db, user_id=user.id)

        if analysis:
            await query.edit_message_text(analysis, reply_markup=_MENU_KB)
        else:
            await query.edit_message_text("❌ Не удалось сгенерировать анализ. Попробуй позже.", reply_markup=_MENU_KB)

    except Exception as e:
        logger.error(f"handle_report_analysis_callback error: {e}", exc_info=True)
        await query.edit_message_text("❌ Произошла ошибка при анализе.")
