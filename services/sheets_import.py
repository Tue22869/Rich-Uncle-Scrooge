"""Import transactions from Google Sheets back into database."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from services.google_sheets_client import read_sheet_values

logger = logging.getLogger(__name__)


class ImportedTransaction:
    """Represents a transaction parsed from Sheets."""

    def __init__(
        self,
        operation_date: datetime,
        transaction_type: str,
        amount: Decimal,
        currency: str,
        account_name: str,
        category: Optional[str] = None,
        description: Optional[str] = None,
    ):
        self.operation_date = operation_date
        self.transaction_type = transaction_type
        self.amount = amount
        self.currency = currency
        self.account_name = account_name
        self.category = category
        self.description = description

    def __repr__(self):
        return (
            f"ImportedTransaction({self.operation_date.date()}, "
            f"{self.transaction_type}, {self.amount} {self.currency}, "
            f"{self.account_name}, {self.category or ''}, {self.description or ''})"
        )


class ImportedAccount:
    """Represents an account parsed from Sheets."""

    def __init__(self, name: str, currency: str, initial_balance: Decimal = Decimal("0"), is_default: bool = False):
        self.name = name
        self.currency = currency
        self.initial_balance = initial_balance
        self.is_default = is_default

    def __repr__(self):
        return f"ImportedAccount({self.name}, {self.currency}, balance={self.initial_balance}, default={self.is_default})"


def parse_accounts_from_balances_sheet(spreadsheet_id: str) -> list[ImportedAccount]:
    """Read and parse accounts from "Балансы" sheet.

    Expected format:
    Счет | Валюта | Баланс | Основной

    Returns list of ImportedAccount objects (balance is ignored).
    """
    from services.sheets_export import build_balances_sheet_title
    
    sheet_title = build_balances_sheet_title()
    
    try:
        values = read_sheet_values(spreadsheet_id, sheet_title)
    except Exception as e:
        logger.warning(f"Failed to read {sheet_title} sheet: {e}")
        return []
    
    if not values:
        return []

    accounts = []
    
    for i, row in enumerate(values):
        if not row:
            continue
        
        # Skip header rows (instruction, empty, column headers)
        if i < 3:
            continue
        
        first_cell = str(row[0]).strip()
        
        # Skip empty/placeholder rows
        if not first_cell or first_cell in {"Нет счетов", "(no accounts)"}:
            continue
        
        # Parse account row: Счет | Валюта | Баланс | Основной
        if len(row) >= 2:
            try:
                name = str(row[0]).strip()
                currency = str(row[1]).strip().upper()
                
                # Parse initial balance if provided
                initial_balance = Decimal("0")
                if len(row) > 2 and row[2]:
                    try:
                        balance_str = str(row[2]).strip().replace(" ", "").replace(",", ".")
                        initial_balance = Decimal(balance_str)
                    except:
                        pass  # Keep 0 if parsing fails
                
                # Parse is_default (column 4: "Основной")
                is_default = False
                if len(row) > 3 and row[3]:
                    default_str = str(row[3]).strip().upper()
                    is_default = "ДА" in default_str or "⭐" in default_str
                
                if name and currency:
                    accounts.append(ImportedAccount(name, currency, initial_balance, is_default))
            except Exception as e:
                logger.warning(f"Failed to parse account row {row}: {e}")
                continue

    return accounts


def parse_transactions_from_month_sheets(
    spreadsheet_id: str, db: Session, user_id: int
) -> list[ImportedTransaction]:
    """Read and parse transactions from monthly sheets (YYYY-MM).

    Expected format in each sheet:
    Дата | Тип | Сумма | Валюта | Счет | Категория | Описание

    Returns list of ImportedTransaction objects from all monthly sheets.
    """
    from services.sheets_export import get_user_transaction_months, build_month_sheet_title
    
    # Get list of all months to check
    months = get_user_transaction_months(db, user_id)
    
    all_transactions = []
    
    for year, month in months:
        sheet_title = build_month_sheet_title(year, month)
        
        try:
            values = read_sheet_values(spreadsheet_id, sheet_title)
        except Exception as e:
            logger.warning(f"Failed to read {sheet_title} sheet: {e}")
            continue
        
        if not values:
            continue
        
        for i, row in enumerate(values):
            if not row:
                continue
            
            # Skip header rows (instruction, empty, column headers)
            if i < 3:
                continue
            
            first_cell = str(row[0]).strip()
            
            # Skip empty/placeholder rows and summary rows
            if not first_cell or "Нет операций" in first_cell or "═" in first_cell:
                continue
            
            # Skip summary rows (they now appear in columns I-J, but might leak into parsing)
            if "ИТОГО" in first_cell or "💰" in first_cell or "💸" in first_cell or "📈" in first_cell or "📂" in first_cell:
                continue
            
            # Parse transaction row
            if len(row) >= 5:
                try:
                    tx = _parse_transaction_row(row)
                    if tx:
                        all_transactions.append(tx)
                        logger.debug(f"Parsed transaction: {tx.operation_date.date()} {tx.transaction_type} {tx.amount} {tx.currency}")
                except Exception as e:
                    logger.warning(f"Failed to parse row {row}: {e}")
                    continue
    
    return all_transactions


def _parse_transaction_row(row: list) -> Optional[ImportedTransaction]:
    """Parse single transaction row.

    Format: [date, type, amount, currency, account, category?, description?]
    """
    if len(row) < 5:
        return None

    date_str = str(row[0]).strip()
    type_str = str(row[1]).strip().lower()
    amount_str = str(row[2]).strip()
    currency = str(row[3]).strip().upper()
    account_name = str(row[4]).strip()
    category = str(row[5]).strip() if len(row) > 5 else None
    description = str(row[6]).strip() if len(row) > 6 else None

    # Skip empty/invalid rows
    if not date_str or not amount_str or not account_name:
        return None

    # Parse date (support DD.MM.YYYY HH:MM, DD.MM.YYYY, or YYYY-MM-DD)
    try:
        if "." in date_str:
            # Check if time is included
            if " " in date_str and ":" in date_str:
                operation_date = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
            else:
                operation_date = datetime.strptime(date_str, "%d.%m.%Y")
        elif "-" in date_str and len(date_str) == 10:
            operation_date = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            return None
    except ValueError:
        logger.warning(f"Invalid date format: {date_str}")
        return None

    # Parse amount (support "1 234,56" or "1234.56")
    try:
        amount_clean = amount_str.replace(" ", "").replace(",", ".")
        # Remove currency symbols
        amount_clean = amount_clean.replace("₽", "").replace("$", "").replace("€", "")
        amount = Decimal(amount_clean)
    except Exception:
        logger.warning(f"Invalid amount: {amount_str}")
        return None

    # Determine transaction type
    transaction_type = None
    if type_str in {"доход", "income", "+", "💰"}:
        transaction_type = "income"
    elif type_str in {"расход", "expense", "-", "💸"}:
        transaction_type = "expense"
    else:
        logger.warning(f"Unknown transaction type: {type_str}")
        return None

    return ImportedTransaction(
        operation_date=operation_date,
        transaction_type=transaction_type,
        amount=amount,
        currency=currency,
        account_name=account_name,
        category=category if category and category != "—" else None,
        description=description if description and description != "—" else None,
    )
