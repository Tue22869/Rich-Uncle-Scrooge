"""Insights service for analytical questions."""
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from sqlalchemy import func, and_, desc
from sqlalchemy.orm import Session

from db.models import User, Account, Transaction, TransactionType
from utils.dates import parse_period, get_prev_period, format_date
from utils.money import format_amount

logger = logging.getLogger(__name__)


def get_insight(
    db: Session,
    user_id: int,
    metric: str,  # "expense", "income", "net"
    category: Optional[str],
    period_preset: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    compare_to: str = "prev_month",
    account_name: Optional[str] = None,
    currency: Optional[str] = None,
    user_timezone: str = "Europe/London"
) -> Dict:
    """
    Generate insight explanation.
    
    Returns:
        {
            "current_total": Decimal,
            "baseline_total": Decimal,
            "delta": Decimal,
            "delta_pct": float,
            "top_transactions": [Transaction],
            "top_days": [{"date": datetime, "amount": Decimal}],
            "top_merchants": [{"description": str, "amount": Decimal}],
            "period": {"from": datetime, "to": datetime},
            "baseline_period": {"from": datetime, "to": datetime}
        }
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    
    # Parse current period
    start, end = parse_period(period_preset, from_date, to_date, user.timezone)
    
    # Get baseline period
    if compare_to == "prev_period":
        baseline_start, baseline_end = get_prev_period(period_preset or "month", user.timezone)
    elif compare_to == "prev_month":
        baseline_start, baseline_end = get_prev_period("month", user.timezone)
    elif compare_to == "prev_year":
        baseline_start, baseline_end = get_prev_period("year", user.timezone)
    elif compare_to == "avg_3m":
        # Average of last 3 months
        baseline_start = start - timedelta(days=90)
        baseline_end = start
    else:
        baseline_start = None
        baseline_end = None
    
    # Build query filters
    filters = [
        Transaction.user_id == user_id,
        Transaction.operation_date >= start,
        Transaction.operation_date <= end
    ]
    
    if metric == "expense":
        filters.append(Transaction.type == TransactionType.EXPENSE)
    elif metric == "income":
        filters.append(Transaction.type == TransactionType.INCOME)
    # For "net", we'll calculate separately
    
    if category:
        filters.append(Transaction.category == category)
    
    if account_name:
        account = db.query(Account).filter(
            Account.user_id == user_id,
            Account.name.ilike(f"%{account_name}%")
        ).first()
        if account:
            if metric == "expense" or metric == "net":
                filters.append(Transaction.account_id == account.id)
            elif metric == "income":
                filters.append(Transaction.account_id == account.id)
    
    if currency:
        filters.append(Transaction.currency == currency)
    
    # Get current total
    current_total = db.query(func.sum(Transaction.amount)).filter(*filters).scalar() or Decimal("0.00")
    
    # Get baseline total
    baseline_total = Decimal("0.00")
    if baseline_start and baseline_end:
        baseline_filters = filters.copy()
        baseline_filters[1] = Transaction.operation_date >= baseline_start
        baseline_filters[2] = Transaction.operation_date <= baseline_end
        baseline_total = db.query(func.sum(Transaction.amount)).filter(*baseline_filters).scalar() or Decimal("0.00")
    
    # Calculate delta
    delta = current_total - baseline_total
    delta_pct = 0.0
    if baseline_total > 0:
        delta_pct = float((delta / baseline_total) * 100)
    
    # Get top transactions
    top_transactions = db.query(Transaction).filter(*filters).order_by(
        desc(Transaction.amount)
    ).limit(10).all()
    
    # Get top days (aggregate by date)
    top_days_raw = db.query(
        func.date(Transaction.operation_date).label("date"),
        func.sum(Transaction.amount).label("total")
    ).filter(*filters).group_by(
        func.date(Transaction.operation_date)
    ).order_by(desc("total")).limit(10).all()
    
    top_days = [
        {"date": row.date, "amount": row.total}
        for row in top_days_raw
    ]
    
    # Get top merchants/descriptions (if available)
    top_merchants_raw = db.query(
        Transaction.description,
        func.sum(Transaction.amount).label("total")
    ).filter(
        *filters,
        Transaction.description.isnot(None),
        Transaction.description != ""
    ).group_by(Transaction.description).order_by(desc("total")).limit(10).all()
    
    top_merchants = [
        {"description": row.description, "amount": row.total}
        for row in top_merchants_raw
    ]
    
    return {
        "current_total": current_total,
        "baseline_total": baseline_total,
        "delta": delta,
        "delta_pct": round(delta_pct, 1),
        "top_transactions": top_transactions,
        "top_days": top_days,
        "top_merchants": top_merchants,
        "period": {"from": start, "to": end},
        "baseline_period": {"from": baseline_start, "to": baseline_end} if baseline_start else None,
        "metric": metric,
        "category": category,
        "currency": currency
    }


def format_insight_text(insight: Dict, user_timezone: str = "Europe/London") -> str:
    """Format insight as text message."""
    lines = []
    
    metric_name = {
        "expense": "расходы",
        "income": "доходы",
        "net": "сальдо"
    }.get(insight["metric"], "расходы")
    
    category_str = f" на {insight['category']}" if insight.get("category") else ""
    currency = insight.get("currency") or "RUB"
    
    period = insight["period"]
    start_str = format_date(period["from"])
    end_str = format_date(period["to"])
    
    current = insight["current_total"]
    baseline = insight["baseline_total"]
    delta = insight["delta"]
    delta_pct = insight["delta_pct"]
    
    # Main fact
    if baseline > 0:
        if delta > 0:
            lines.append(
                f"📊 Ты потратил{category_str} в период {start_str}–{end_str}: "
                f"{format_amount(current, currency)}. "
                f"Это на {format_amount(abs(delta), currency)} ({abs(delta_pct)}%) больше, "
                f"чем в предыдущем периоде ({format_amount(baseline, currency)})."
            )
        elif delta < 0:
            lines.append(
                f"📊 Ты потратил{category_str} в период {start_str}–{end_str}: "
                f"{format_amount(current, currency)}. "
                f"Это на {format_amount(abs(delta), currency)} ({abs(delta_pct)}%) меньше, "
                f"чем в предыдущем периоде ({format_amount(baseline, currency)})."
            )
        else:
            lines.append(
                f"📊 Ты потратил{category_str} в период {start_str}–{end_str}: "
                f"{format_amount(current, currency)}. "
                f"Это столько же, сколько в предыдущем периоде."
            )
    else:
        lines.append(
            f"📊 Ты потратил{category_str} в период {start_str}–{end_str}: "
            f"{format_amount(current, currency)}. "
            f"Раньше нет данных для сравнения."
        )
    
    lines.append("")
    
    # Top transactions
    top_transactions = insight["top_transactions"]
    if top_transactions:
        lines.append("🔝 Основной вклад дали:")
        for i, txn in enumerate(top_transactions[:5], 1):
            desc = txn.description or txn.category or "Без описания"
            lines.append(
                f"  {i}. {desc} — {format_amount(txn.amount, txn.currency)} "
                f"({format_date(txn.operation_date)})"
            )
        lines.append("")
    
    # Top days
    top_days = insight["top_days"]
    if top_days:
        lines.append("📅 Пик был:")
        for day_info in top_days[:5]:
            lines.append(
                f"  • {format_date(day_info['date'])} — {format_amount(day_info['amount'], currency)}"
            )
        lines.append("")
    
    # Top merchants
    top_merchants = insight["top_merchants"]
    if top_merchants:
        lines.append("🏪 По местам:")
        for i, merch in enumerate(top_merchants[:5], 1):
            lines.append(f"  {i}. {merch['description']} — {format_amount(merch['amount'], currency)}")
        lines.append("")
    
    return "\n".join(lines)

