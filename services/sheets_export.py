"""Export SmartFinances data to Google Sheets format (multi-sheet design).

Design:
- Sheet "Балансы": account balances
- Sheet "YYYY-MM" (e.g. "2026-01"): transactions for that month
"""

from __future__ import annotations

import logging
from decimal import Decimal
from datetime import datetime
from collections import defaultdict
from typing import List, Tuple
from calendar import monthrange

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import User, Account, Transaction, TransactionType

logger = logging.getLogger(__name__)


def build_balances_sheet_title() -> str:
    """Sheet title for account balances."""
    return "Балансы"


def build_month_sheet_title(year: int, month: int) -> str:
    """Sheet title for monthly transactions (YYYY-MM format)."""
    return f"{year:04d}-{month:02d}"


def build_balances_export(db: Session, user_id: int) -> List[List[object]]:
    """Build balances sheet data.
    
    Format:
    Счет | Валюта | Баланс | Основной
    """
    user = db.query(User).filter(User.id == user_id).first()
    accounts = db.query(Account).filter(Account.user_id == user_id).order_by(Account.created_at).all()
    
    values = []
    
    # Compact header
    values.append(["БАЛАНСЫ СЧЕТОВ — можно редактировать: Счет, Валюта. Можно ДОБАВЛЯТЬ новые строки для создания счетов. Баланс автоматический. Импорт: /sheets_import"])
    values.append([""])
    
    values.append(["Счет", "Валюта", "Баланс", "Основной"])
    
    if not accounts:
        values.append(["Нет счетов", "", "", ""])
    else:
        for acc in accounts:
            # Check if this account is default
            is_default = (user and user.default_account_id == acc.id)
            
            values.append([
                acc.name,
                acc.currency,
                float(acc.balance),
                "⭐ ДА" if is_default else "НЕТ"
            ])
    
    return values


def build_month_transactions_export(
    db: Session, user_id: int, year: int, month: int
) -> List[List[object]]:
    """Build monthly transactions sheet data with summary.
    
    Format:
    Дата | Тип | Сумма | Валюта | Счет | Категория | Описание
    """
    start_date = datetime(year, month, 1)
    last_day = monthrange(year, month)[1]
    end_date = datetime(year, month, last_day, 23, 59, 59)
    
    transactions = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.operation_date >= start_date,
            Transaction.operation_date <= end_date
        )
        .order_by(Transaction.operation_date.desc())
        .all()
    )
    
    values = []
    
    # Compact header
    values.append([f"ОПЕРАЦИИ ЗА {year}-{month:02d} — можно редактировать ВСЕ и добавлять строки. Импорт: /sheets_import", "", "", "", "", "", "", "", "ИТОГИ"])
    values.append([""])
    
    # Calculate totals by currency and category FIRST
    income_by_currency = defaultdict(Decimal)
    expense_by_currency = defaultdict(Decimal)
    income_by_category = defaultdict(lambda: defaultdict(Decimal))
    expense_by_category = defaultdict(lambda: defaultdict(Decimal))
    
    for tx in transactions:
        if tx.type == TransactionType.INCOME:
            income_by_currency[tx.currency] += tx.amount
            category = tx.category or "Без категории"
            income_by_category[category][tx.currency] += tx.amount
        elif tx.type == TransactionType.EXPENSE:
            expense_by_currency[tx.currency] += tx.amount
            category = tx.category or "Без категории"
            expense_by_category[category][tx.currency] += tx.amount
    
    # Build header with summary columns on the right
    values.append(["Дата", "Тип", "Сумма", "Валюта", "Счет", "Категория", "Описание", "", "📊 ИТОГИ ЗА МЕСЯЦ"])
    
    # Build rows with summary in columns I-J (just label and value)
    summary_rows = []
    all_currencies = sorted(set(income_by_currency.keys()) | set(expense_by_currency.keys()))
    for currency in all_currencies:
        income = income_by_currency.get(currency, Decimal("0"))
        expense = expense_by_currency.get(currency, Decimal("0"))
        net = income - expense
        summary_rows.append([f"💰 Доходы ({currency}):", float(income)])
        summary_rows.append([f"💸 Расходы ({currency}):", float(expense)])
        summary_rows.append([f"📈 Сальдо ({currency}):", float(net)])
        summary_rows.append(["", ""])
    
    # Add category breakdown to summary
    if expense_by_category:
        summary_rows.append(["📂 По категориям:", ""])
        for category, currencies in sorted(expense_by_category.items(), 
                                          key=lambda x: sum(x[1].values()), reverse=True):
            for currency, amount in sorted(currencies.items()):
                summary_rows.append([f"  {category}", float(amount)])
    
    if not transactions:
        values.append([f"Нет операций за {year}-{month:02d}", "", "", "", "", "", ""])
    else:
        row_idx = 0
        for tx in transactions:
            tx_type = "доход" if tx.type == TransactionType.INCOME else "расход"
            # Include time (MSK timezone) in export
            date_str = tx.operation_date.strftime("%d.%m.%Y %H:%M")
            
            account_name = "—"
            if tx.account_id:
                acc = db.query(Account).filter(Account.id == tx.account_id).first()
                if acc:
                    account_name = acc.name
            
            category = tx.category or "Без категории"
            description = tx.description or "—"
            
            # Add transaction row + summary column if available
            tx_row = [
                date_str,
                tx_type,
                float(tx.amount),
                tx.currency,
                account_name,
                category,
                description,
                ""  # Empty column separator
            ]
            
            # Add summary data if available for this row
            if row_idx < len(summary_rows):
                tx_row.extend(summary_rows[row_idx])
            
            values.append(tx_row)
            row_idx += 1
        
        # Add remaining summary rows if there are more summary lines than transactions
        while row_idx < len(summary_rows):
            values.append(["", "", "", "", "", "", "", ""] + summary_rows[row_idx])
            row_idx += 1
    
    return values


def get_user_transaction_months(db: Session, user_id: int, limit: int = None) -> List[Tuple[int, int]]:
    """Get list of (year, month) tuples for user's transactions (most recent first).
    
    Args:
        db: Database session
        user_id: User ID
        limit: Optional limit on number of months (None = all months)
    """
    query = (
        db.query(
            func.extract('year', Transaction.operation_date).label('year'),
            func.extract('month', Transaction.operation_date).label('month')
        )
        .filter(Transaction.user_id == user_id)
        .distinct()
        .order_by(
            func.extract('year', Transaction.operation_date).desc(),
            func.extract('month', Transaction.operation_date).desc()
        )
    )
    
    if limit:
        query = query.limit(limit)
    
    result = query.all()
    
    return [(int(row.year), int(row.month)) for row in result]
