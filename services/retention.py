"""Retention service: digests, streaks, achievements, budget alerts."""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict

from sqlalchemy.orm import Session

from db.models import User, Account, Transaction, TransactionType, Budget
from services.reports import get_report
from utils.money import format_amount

logger = logging.getLogger(__name__)

# Achievement definitions: code -> (label, description, check_func_name)
ACHIEVEMENTS = {
    "first_op": ("🏅 Первая операция", "Записал первую операцию"),
    "ops_10": ("🎯 10 операций", "Записал 10 операций"),
    "ops_100": ("💯 Сотня!", "Записал 100 операций"),
    "ops_500": ("🏆 500 операций", "Записал 500 операций"),
    "streak_7": ("🔥 Неделя подряд", "Записывал расходы 7 дней подряд"),
    "streak_30": ("🔥🔥 Месяц подряд", "Записывал расходы 30 дней подряд"),
    "streak_100": ("🔥🔥🔥 Сто дней", "Записывал расходы 100 дней подряд"),
    "saved_10k": ("💰 Сберёг 10к", "Накопил 10 000₽ за месяц"),
    "multi_account": ("💳 Мультисчёт", "Создал 3+ счетов"),
    "first_month": ("📅 Первый месяц", "Используете бота больше месяца"),
}

# Financial tips (rotated by day)
FINANCIAL_TIPS = [
    "💡 Записывайте расходы сразу после покупки — так ничего не забудется.",
    "💡 Правило 50/30/20: 50% на нужды, 30% на желания, 20% на сбережения.",
    "💡 Ведите «список импульсных покупок» — подождите 48 часов перед покупкой.",
    "💡 Автоматизируйте сбережения: переводите 10-20% зарплаты сразу на накопительный счёт.",
    "💡 Сравнивайте расходы по месяцам — тренды важнее абсолютных цифр.",
    "💡 Кофе за 300₽ каждый день = 9 000₽/мес = 108 000₽/год. Мелочи складываются!",
    "💡 Подписки — тихий убийца бюджета. Проверяйте раз в месяц, от чего можно отказаться.",
    "💡 Финансовая подушка = 3-6 месячных расходов. Начните с малого — даже 1000₽/мес помогут.",
    "💡 Не путайте «могу себе позволить» с «мне это нужно».",
    "💡 Обедать из дома 3 раза в неделю вместо кафе — экономия ~15 000₽/мес.",
    "💡 Если покупка не радует через неделю — это был импульс, а не потребность.",
    "💡 Отслеживайте «стоимость часа» — это помогает оценить, стоит ли экономить на чём-то.",
    "💡 Установите бюджет на категорию и следите: «бюджет на кофе 3000₽ в месяц».",
    "💡 Анализируйте не только расходы, но и доходы — ищите возможности для роста.",
]


def get_tip_of_the_day() -> str:
    """Get a financial tip based on the current day."""
    day_index = datetime.utcnow().timetuple().tm_yday % len(FINANCIAL_TIPS)
    return FINANCIAL_TIPS[day_index]


def update_streak(db: Session, user: User) -> Dict:
    """
    Update user's streak after recording an operation.
    Returns dict with streak info and any new achievements.
    """
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    new_achievements = []

    if user.last_activity_date == today_str:
        # Already recorded today — no streak change
        return {"streak": user.streak_days, "new_achievements": []}

    yesterday_str = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    if user.last_activity_date == yesterday_str:
        user.streak_days += 1
    elif user.last_activity_date is None:
        user.streak_days = 1
    else:
        user.streak_days = 1  # streak broken

    user.last_activity_date = today_str
    user.total_operations += 1

    # Check achievements
    current = set(user.achievements_json or [])

    checks = [
        ("first_op", user.total_operations >= 1),
        ("ops_10", user.total_operations >= 10),
        ("ops_100", user.total_operations >= 100),
        ("ops_500", user.total_operations >= 500),
        ("streak_7", user.streak_days >= 7),
        ("streak_30", user.streak_days >= 30),
        ("streak_100", user.streak_days >= 100),
    ]

    # Check multi-account
    account_count = db.query(Account).filter(Account.user_id == user.id).count()
    checks.append(("multi_account", account_count >= 3))

    # Check first month
    if user.created_at:
        days_since_creation = (datetime.utcnow() - user.created_at).days
        checks.append(("first_month", days_since_creation >= 30))

    # Check saved_10k: net income >= 10000 RUB this month
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_txns = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.operation_date >= month_start,
            Transaction.currency == "RUB",
        )
        .all()
    )
    month_income = sum(tx.amount for tx in month_txns if tx.type == TransactionType.INCOME)
    month_expense = sum(tx.amount for tx in month_txns if tx.type == TransactionType.EXPENSE)
    checks.append(("saved_10k", (month_income - month_expense) >= Decimal("10000")))

    for code, condition in checks:
        if condition and code not in current:
            current.add(code)
            new_achievements.append(code)

    user.achievements_json = list(current)
    db.commit()

    return {
        "streak": user.streak_days,
        "new_achievements": new_achievements,
    }


def format_achievement_notification(achievement_codes: List[str]) -> str:
    """Format achievement notification text."""
    if not achievement_codes:
        return ""

    lines = ["🏆 *Новые достижения!*\n"]
    for code in achievement_codes:
        if code in ACHIEVEMENTS:
            label, desc = ACHIEVEMENTS[code]
            lines.append(f"{label} — {desc}")

    return "\n".join(lines)


async def _llm_rewrite_digest(
    template_text: str,
    digest_type: str = "weekly",
    *,
    db: Optional[Session] = None,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """Use LLM to rewrite a template digest into natural, motivational text."""
    try:
        from llm.parser import client, PRIMARY_MODEL, _extract_usage
        period_label = "еженедельный" if digest_type == "weekly" else "ежемесячный"
        system_prompt = (
            f"Ты — дружелюбный финансовый ассистент. Перепиши {period_label} дайджест "
            "в живой, мотивирующий текст на русском. Сохрани все цифры и факты. "
            "Добавь эмодзи, позитивные комментарии если есть экономия, мягкие "
            "предупреждения если расходы выросли. Используй Markdown (*bold*). "
            "Длина: 5-15 строк. Начни с заголовка."
        )
        response = await client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": template_text},
            ],
            temperature=0.7,
        )

        # Best-effort cost accounting.
        if db is not None and user_id is not None:
            try:
                from services.analytics import log_event
                from db.models import UsageEventKind
                usage = _extract_usage(response)
                log_event(
                    db,
                    user_id=user_id,
                    kind=UsageEventKind.DIGEST_LLM,
                    model=PRIMARY_MODEL,
                    **usage,
                    meta={"period": digest_type},
                )
            except Exception as e:
                logger.warning(f"digest usage logging failed: {e}")

        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"LLM digest rewrite failed, using template: {e}")
        return None


def format_streak_text(streak_days: int) -> str:
    """Format streak display text."""
    if streak_days <= 0:
        return ""
    if streak_days == 1:
        return "🔥 1 день записей подряд"
    return f"🔥 {streak_days} дней записей подряд"


def generate_weekly_digest(db: Session, user: User) -> Optional[str]:
    """Generate weekly digest text for a user."""
    now = datetime.utcnow()
    this_week_start = now - timedelta(days=now.weekday(), hours=now.hour, minutes=now.minute)
    last_week_start = this_week_start - timedelta(days=7)

    this_week_report = get_report(
        db, user.id,
        period_preset="week",
        user_timezone=user.timezone,
    )
    last_week_report = get_report(
        db, user.id,
        from_date=last_week_start.strftime("%Y-%m-%d"),
        to_date=this_week_start.strftime("%Y-%m-%d"),
        user_timezone=user.timezone,
    )

    tw_totals = this_week_report.get("totals", {})
    lw_totals = last_week_report.get("totals", {})

    # Check if there's any data
    has_data = False
    for currency_data in tw_totals.values():
        if currency_data.get("expense", 0) > 0 or currency_data.get("income", 0) > 0:
            has_data = True
            break

    if not has_data:
        return None

    lines = ["📊 *Еженедельный дайджест*\n"]

    for currency, data in tw_totals.items():
        tw_expense = Decimal(str(data.get("expense", 0)))
        tw_income = Decimal(str(data.get("income", 0)))

        lw_data = lw_totals.get(currency, {})
        lw_expense = Decimal(str(lw_data.get("expense", 0)))

        lines.append(f"💰 {currency}:")
        lines.append(f"  Доходы: {format_amount(tw_income, currency)}")
        lines.append(f"  Расходы: {format_amount(tw_expense, currency)}")

        if lw_expense > 0:
            diff = tw_expense - lw_expense
            if diff > 0:
                lines.append(f"  📈 На {format_amount(diff, currency)} больше, чем на прошлой неделе")
            elif diff < 0:
                lines.append(f"  📉 На {format_amount(abs(diff), currency)} меньше — так держать! 💪")
            else:
                lines.append("  ➡️ Столько же, как на прошлой неделе")

    # Top categories
    categories = this_week_report.get("expense_categories", {})
    if categories:
        lines.append("\n📂 Топ-3 категории расходов:")
        sorted_cats = sorted(categories.items(), key=lambda x: float(x[1].get("total", 0)), reverse=True)[:3]
        for i, (cat, cat_data) in enumerate(sorted_cats, 1):
            total = cat_data.get("total", 0)
            currency = cat_data.get("currency", "RUB")
            lines.append(f"  {i}. {cat}: {format_amount(Decimal(str(total)), currency)}")

    # Streak
    if user.streak_days > 1:
        lines.append(f"\n{format_streak_text(user.streak_days)}")

    # Tip
    lines.append(f"\n{get_tip_of_the_day()}")

    return "\n".join(lines)


def generate_monthly_digest(db: Session, user: User) -> Optional[str]:
    """Generate monthly digest text for a user."""
    this_month_report = get_report(
        db, user.id,
        period_preset="month",
        user_timezone=user.timezone,
    )

    last_month_report = get_report(
        db, user.id,
        period_preset="last_month",
        user_timezone=user.timezone,
    )

    tm_totals = this_month_report.get("totals", {})
    lm_totals = last_month_report.get("totals", {})

    has_data = False
    for currency_data in tm_totals.values():
        if currency_data.get("expense", 0) > 0 or currency_data.get("income", 0) > 0:
            has_data = True
            break

    if not has_data:
        return None

    lines = ["📊 *Ежемесячный дайджест*\n"]

    for currency, data in tm_totals.items():
        tm_expense = Decimal(str(data.get("expense", 0)))
        tm_income = Decimal(str(data.get("income", 0)))
        net = tm_income - tm_expense

        lm_data = lm_totals.get(currency, {})
        lm_expense = Decimal(str(lm_data.get("expense", 0)))

        lines.append(f"💰 {currency}:")
        lines.append(f"  Доходы: {format_amount(tm_income, currency)}")
        lines.append(f"  Расходы: {format_amount(tm_expense, currency)}")
        lines.append(f"  Сальдо: {'+' if net >= 0 else ''}{format_amount(net, currency)}")

        if lm_expense > 0:
            diff = tm_expense - lm_expense
            pct = (diff / lm_expense * 100) if lm_expense else Decimal("0")
            if diff > 0:
                lines.append(f"  📈 Расходы выросли на {abs(pct):.0f}% vs прошлый месяц")
            elif diff < 0:
                lines.append(f"  📉 Сэкономили {abs(pct):.0f}% vs прошлый месяц 💪")

    # Category comparison
    tm_cats = this_month_report.get("expense_categories", {})
    lm_cats = last_month_report.get("expense_categories", {})

    saved_cats = []
    overspent_cats = []

    for cat in tm_cats:
        tm_total = float(tm_cats[cat].get("total", 0))
        lm_total = float(lm_cats.get(cat, {}).get("total", 0))
        if lm_total > 0:
            diff = tm_total - lm_total
            if diff < -100:
                saved_cats.append((cat, abs(diff)))
            elif diff > 100:
                overspent_cats.append((cat, diff))

    if saved_cats:
        lines.append("\n✅ Сэкономили:")
        for cat, amount in sorted(saved_cats, key=lambda x: x[1], reverse=True)[:3]:
            lines.append(f"  • {cat}: −{amount:,.0f}")

    if overspent_cats:
        lines.append("\n⚠️ Перерасход:")
        for cat, amount in sorted(overspent_cats, key=lambda x: x[1], reverse=True)[:3]:
            lines.append(f"  • {cat}: +{amount:,.0f}")

    return "\n".join(lines)


async def generate_weekly_digest_llm(db: Session, user: User) -> Optional[str]:
    """Generate weekly digest with LLM rewrite. Falls back to template."""
    template = generate_weekly_digest(db, user)
    if not template:
        return None
    llm_text = await _llm_rewrite_digest(template, "weekly", db=db, user_id=user.id)
    return llm_text or template


async def generate_monthly_digest_llm(db: Session, user: User) -> Optional[str]:
    """Generate monthly digest with LLM rewrite. Falls back to template."""
    template = generate_monthly_digest(db, user)
    if not template:
        return None
    llm_text = await _llm_rewrite_digest(template, "monthly", db=db, user_id=user.id)
    return llm_text or template


def check_budget_alerts(db: Session, user: User) -> List[str]:
    """Check if any budget limits are approaching or exceeded. Returns alert messages."""
    budgets = db.query(Budget).filter(Budget.user_id == user.id).all()
    if not budgets:
        return []

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    alerts = []
    for budget in budgets:
        # Sum expenses in this category for current month
        expenses = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == user.id,
                Transaction.type == TransactionType.EXPENSE,
                Transaction.category == budget.category,
                Transaction.operation_date >= month_start,
            )
            .all()
        )

        total_spent = sum(tx.amount for tx in expenses if tx.currency == budget.currency)
        limit = budget.monthly_limit

        if total_spent >= limit:
            pct = int(total_spent / limit * 100) if limit > 0 else 100
            alerts.append(
                f"🚨 *Бюджет «{budget.category}»*: потрачено {format_amount(total_spent, budget.currency)} "
                f"из {format_amount(limit, budget.currency)} ({pct}%) — лимит превышен!"
            )
        elif total_spent >= limit * Decimal("0.8"):
            pct = int(total_spent / limit * 100) if limit > 0 else 0
            alerts.append(
                f"⚠️ *Бюджет «{budget.category}»*: потрачено {format_amount(total_spent, budget.currency)} "
                f"из {format_amount(limit, budget.currency)} ({pct}%)"
            )

    return alerts


def get_users_needing_reminder(db: Session, days_inactive: int = 3) -> List[User]:
    """Get users who haven't recorded anything in N+ days."""
    from bot.middleware import _has_premium_access

    cutoff = (datetime.utcnow() - timedelta(days=days_inactive)).strftime("%Y-%m-%d")
    users = (
        db.query(User)
        .filter(
            User.last_activity_date.isnot(None),
            User.last_activity_date < cutoff,
        )
        .all()
    )

    # Only remind premium users
    return [u for u in users if _has_premium_access(db, u)]
