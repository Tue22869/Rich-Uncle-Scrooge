"""Shared helpers: DB access, validation, preview builders, operation execution."""
import logging
from decimal import Decimal
from datetime import datetime

from sqlalchemy.orm import Session

from db.models import User, Account
from db.session import SessionLocal
from services.ledger import (
    find_account_by_name, add_income, add_expense,
    transfer, create_account, delete_account, rename_account,
    set_default_account, update_transaction, delete_transaction_by_id,
    count_account_transactions
)
from utils.dates import format_operation_date
from utils.money import format_amount

logger = logging.getLogger(__name__)


def get_db() -> Session:
    """Get database session."""
    return SessionLocal()


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


def build_preview_text(db: Session, user: User, intent: str, data) -> str:
    """Build preview text for pending action."""
    lines = []

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

        if data.to_amount and data.to_currency:
            lines.append(f"  {to_acc}: +{format_amount(Decimal(str(data.to_amount)), data.to_currency)}")
        else:
            lines.append(f"  {to_acc}: +{format_amount(Decimal(str(amount)), currency)}")

    elif intent == "account_add":
        acc_new = data.account_new
        lines.append("💳 Создать счёт:")
        lines.append(f"  Название: {acc_new.name}")
        lines.append(f"  Валюта: {acc_new.currency}")
        if acc_new.initial_balance:
            lines.append(f"  Начальный баланс: {format_amount(Decimal(str(acc_new.initial_balance)), acc_new.currency)}")

    elif intent == "account_delete":
        acc = find_account_by_name(db, user.id, data.account_name)
        lines.append("🗑️ Удалить счёт:")
        if acc:
            tx_count = count_account_transactions(db, user.id, acc.id)
            lines.append(f"  {acc.name} ({acc.currency})")
            lines.append(f"  Баланс: {format_amount(acc.balance, acc.currency)}")
            if tx_count > 0:
                lines.append(f"  Вместе со счётом удалится {tx_count} операций.")
            lines.append("  Это необратимо.")
        else:
            lines.append(f"  {data.account_name}")

    elif intent == "account_rename":
        lines.append("✏️ Переименовать счёт:")
        lines.append(f"  {data.account_old_name} → {data.account_new_name}")

    elif intent == "set_default_account":
        lines.append("⭐ Назначить дефолтным счётом:")
        lines.append(f"  {data.account_name}")

    elif intent == "clear_all_data":
        lines.append("⚠️ **УДАЛИТЬ ВСЕ ДАННЫЕ**")
        lines.append("")
        lines.append("Это действие **НЕОБРАТИМО** удалит:")
        lines.append("  • Все счета")
        lines.append("  • Все операции")
        lines.append("  • Всю историю")

    lines.append("\nПодтверди действие кнопками ниже.")

    return "\n".join(lines)


def build_single_operation_preview(intent: str, data, user_timezone: str) -> str:
    """Build preview text for a single operation in batch."""
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
        return "⚠️ УДАЛИТЬ ВСЕ ДАННЫЕ (счета + операции)"

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


def _build_confirmed_text(original_text: str) -> str:
    """Build confirmation message preserving original preview text."""
    text = (original_text or "").rstrip()
    for prompt in ["Подтверди все операции кнопками ниже.",
                    "Подтверди действие кнопками ниже.",
                    "Подтверди действие кнопками ниже.**"]:
        text = text.replace(prompt, "")
    return "✅ Записано\n\n" + text.strip()


def execute_single_operation(db: Session, user: User, intent: str, data_dict: dict):
    """Execute a single operation (used for both regular and batch operations)."""
    from utils.dates import get_user_timezone

    if intent == "income":
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
            tz = get_user_timezone(user.timezone)
            operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
            if not operation_date.tzinfo:
                operation_date = tz.localize(operation_date)

        return add_income(
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
            tz = get_user_timezone(user.timezone)
            operation_date = datetime.fromisoformat(data_dict["operation_date"].replace("Z", "+00:00"))
            if not operation_date.tzinfo:
                operation_date = tz.localize(operation_date)

        return add_expense(
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

        to_amount = None
        to_currency = None
        if data_dict.get("to_amount"):
            to_amount = Decimal(str(data_dict["to_amount"]))
            to_currency = data_dict.get("to_currency")

        return transfer(
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
        return create_account(
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
