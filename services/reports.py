"""Reports service."""
import logging
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import User, Account, Transaction, TransactionType
from utils.dates import parse_period, format_date
from utils.money import format_amount

logger = logging.getLogger(__name__)


def get_total_balances(db: Session, user_id: int) -> Dict[str, Decimal]:
    """Get total balances grouped by currency."""
    accounts = db.query(Account).filter(Account.user_id == user_id).all()
    balances = defaultdict(Decimal)
    
    for account in accounts:
        balances[account.currency] += account.balance
    
    return dict(balances)


def get_report(
    db: Session,
    user_id: int,
    period_preset: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    user_timezone: str = "Europe/London"
) -> Dict:
    """
    Generate financial report for period.
    
    Returns:
        {
            "totals": {"income": Dict[str, Decimal], "expense": Dict[str, Decimal], "net": Dict[str, Decimal]},
            "balances": {"RUB": Decimal, "USD": Decimal, ...},
            "breakdown_income_by_category": [{"category": str, "amount": Decimal, "pct": float, "currency": str}],
            "breakdown_expense_by_category": [{"category": str, "amount": Decimal, "pct": float, "currency": str}],
            "period": {"from": datetime, "to": datetime}
        }
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    
    start, end = parse_period(period_preset, from_date, to_date, user.timezone)
    
    # Get totals grouped by currency
    income_by_currency = db.query(
        Transaction.currency,
        func.sum(Transaction.amount).label("total")
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type == TransactionType.INCOME,
        Transaction.operation_date >= start,
        Transaction.operation_date <= end
    ).group_by(Transaction.currency).all()
    
    expense_by_currency = db.query(
        Transaction.currency,
        func.sum(Transaction.amount).label("total")
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type == TransactionType.EXPENSE,
        Transaction.operation_date >= start,
        Transaction.operation_date <= end
    ).group_by(Transaction.currency).all()
    
    income_totals = {row.currency: row.total for row in income_by_currency}
    expense_totals = {row.currency: row.total for row in expense_by_currency}
    
    # Calculate net by currency
    all_currencies = set(income_totals.keys()) | set(expense_totals.keys())
    net_totals = {}
    for currency in all_currencies:
        income = income_totals.get(currency, Decimal("0.00"))
        expense = expense_totals.get(currency, Decimal("0.00"))
        net_totals[currency] = income - expense
    
    # Get balances
    balances = get_total_balances(db, user_id)
    
    # Breakdown by category (grouped by currency)
    income_by_category = _get_breakdown_by_category(
        db, user_id, TransactionType.INCOME, start, end
    )
    expense_by_category = _get_breakdown_by_category(
        db, user_id, TransactionType.EXPENSE, start, end
    )
    
    return {
        "totals": {
            "income": income_totals,
            "expense": expense_totals,
            "net": net_totals
        },
        "balances": balances,
        "breakdown_income_by_category": income_by_category,
        "breakdown_expense_by_category": expense_by_category,
        "period": {"from": start, "to": end}
    }


def _get_breakdown_by_category(
    db: Session,
    user_id: int,
    transaction_type: TransactionType,
    start: datetime,
    end: datetime
) -> List[Dict]:
    """
    Get breakdown by category, grouped by currency.
    Returns list of {category, amount, pct, currency}
    """
    transactions = db.query(
        Transaction.category,
        Transaction.currency,
        func.sum(Transaction.amount).label("total")
    ).filter(
        Transaction.user_id == user_id,
        Transaction.type == transaction_type,
        Transaction.operation_date >= start,
        Transaction.operation_date <= end
    ).group_by(Transaction.category, Transaction.currency).all()
    
    # Group by currency
    by_currency = defaultdict(list)
    currency_totals = defaultdict(Decimal)
    
    for category, currency, total in transactions:
        category_name = category if category else "Без категории"
        by_currency[currency].append({
            "category": category_name,
            "amount": total,
            "currency": currency
        })
        currency_totals[currency] += total
    
    # Calculate percentages and flatten
    result = []
    for currency, items in by_currency.items():
        total_for_currency = currency_totals[currency]
        for item in items:
            pct = float((item["amount"] / total_for_currency * 100)) if total_for_currency > 0 else 0.0
            item["pct"] = round(pct, 1)
            result.append(item)
    
    # Sort by amount descending
    result.sort(key=lambda x: x["amount"], reverse=True)
    
    return result


def format_report_text(report: Dict, user_timezone: str = "Europe/London") -> str:
    """Format report as text message."""
    period = report["period"]
    start_str = format_date(period["from"])
    end_str = format_date(period["to"])
    
    lines = [f"📊 Отчёт за {start_str}–{end_str}:\n"]
    
    # Totals (grouped by currency)
    totals = report["totals"]
    all_currencies = set(totals['income'].keys()) | set(totals['expense'].keys())
    
    for currency in sorted(all_currencies):
        income = totals['income'].get(currency, Decimal("0.00"))
        expense = totals['expense'].get(currency, Decimal("0.00"))
        net = totals['net'].get(currency, Decimal("0.00"))
        
        if income > 0 or expense > 0:
            lines.append(f"💰 Доходы ({currency}): {format_amount(income, currency)}")
            lines.append(f"💸 Расходы ({currency}): {format_amount(expense, currency)}")
            lines.append(f"📈 Сальдо ({currency}): {format_amount(net, currency)}\n")
    
    # Balances
    balances = report["balances"]
    lines.append("💳 Баланс сейчас (все счета):")
    for currency, amount in sorted(balances.items()):
        lines.append(f"  • {currency}: {format_amount(amount, currency)}")
    lines.append("")
    
    # Income breakdown
    income_breakdown = report["breakdown_income_by_category"]
    if income_breakdown:
        # Group by currency
        by_currency = defaultdict(list)
        for item in income_breakdown:
            by_currency[item["currency"]].append(item)
        
        for currency, items in sorted(by_currency.items()):
            lines.append(f"📥 Откуда пришли (доходы, {currency}):")
            for i, item in enumerate(items[:10], 1):  # Top 10
                lines.append(f"  {i}. {item['category']} — {format_amount(item['amount'], currency)} ({item['pct']}%)")
            if len(items) > 10:
                other = sum(item["amount"] for item in items[10:])
                other_pct = sum(item["pct"] for item in items[10:])
                lines.append(f"  ... Прочее — {format_amount(other, currency)} ({other_pct:.1f}%)")
            lines.append("")
    
    # Expense breakdown
    expense_breakdown = report["breakdown_expense_by_category"]
    if expense_breakdown:
        # Group by currency
        by_currency = defaultdict(list)
        for item in expense_breakdown:
            by_currency[item["currency"]].append(item)
        
        for currency, items in sorted(by_currency.items()):
            lines.append(f"📤 Куда ушли (расходы, {currency}):")
            for i, item in enumerate(items[:10], 1):  # Top 10
                lines.append(f"  {i}. {item['category']} — {format_amount(item['amount'], currency)} ({item['pct']}%)")
            if len(items) > 10:
                other = sum(item["amount"] for item in items[10:])
                other_pct = sum(item["pct"] for item in items[10:])
                lines.append(f"  ... Прочее — {format_amount(other, currency)} ({other_pct:.1f}%)")
            lines.append("")
    
    return "\n".join(lines)

