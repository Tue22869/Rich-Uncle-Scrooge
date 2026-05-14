"""Tests for retention service: streaks, achievements, tips."""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from db.models import User, Budget, Base
from db.session import SessionLocal, init_db, engine
from services.ledger import get_or_create_user, create_account, add_expense
from services.retention import (
    update_streak, format_achievement_notification, format_streak_text,
    get_tip_of_the_day, check_budget_alerts,
)


@pytest.fixture(scope="function")
def db():
    """Create test database session."""
    init_db()
    db = SessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def user(db: Session):
    """Create test user."""
    return get_or_create_user(db, 88888, "Europe/Moscow")


@pytest.fixture
def account(db, user):
    """Create test account with balance."""
    return create_account(db, user.id, "Карта", "RUB", Decimal("100000"))


# === Streak Tests ===

def test_update_streak_first_day(db: Session, user: User):
    """Test streak on first activity."""
    result = update_streak(db, user)
    assert result["streak"] == 1
    assert "first_op" in result["new_achievements"]


def test_update_streak_same_day(db: Session, user: User):
    """Test streak doesn't increment on same day."""
    update_streak(db, user)
    result = update_streak(db, user)
    assert result["streak"] == 1
    assert result["new_achievements"] == []


def test_update_streak_consecutive(db: Session, user: User):
    """Test streak increments on consecutive days."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    user.last_activity_date = yesterday
    user.streak_days = 5
    user.total_operations = 5
    user.achievements_json = ["first_op"]
    db.commit()

    result = update_streak(db, user)
    assert result["streak"] == 6


def test_update_streak_broken(db: Session, user: User):
    """Test streak resets after gap."""
    three_days_ago = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
    user.last_activity_date = three_days_ago
    user.streak_days = 10
    user.total_operations = 10
    user.achievements_json = ["first_op"]
    db.commit()

    result = update_streak(db, user)
    assert result["streak"] == 1  # Reset


def test_streak_7_achievement(db: Session, user: User):
    """Test 7-day streak achievement."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    user.last_activity_date = yesterday
    user.streak_days = 6
    user.total_operations = 6
    user.achievements_json = ["first_op"]
    db.commit()

    result = update_streak(db, user)
    assert "streak_7" in result["new_achievements"]


# === Achievement Formatting ===

def test_format_achievement_empty():
    """Test formatting empty achievements."""
    assert format_achievement_notification([]) == ""


def test_format_achievement_single():
    """Test formatting single achievement."""
    text = format_achievement_notification(["first_op"])
    assert "Первая операция" in text
    assert "🏆" in text


def test_format_streak_text():
    """Test streak text formatting."""
    assert format_streak_text(0) == ""
    assert "1 день" in format_streak_text(1)
    assert "7 дней" in format_streak_text(7)


# === Tip of the Day ===

def test_get_tip_of_the_day():
    """Test tip is returned and starts with 💡."""
    tip = get_tip_of_the_day()
    assert tip.startswith("💡")


# === Budget Alerts ===

def test_budget_alert_no_budgets(db: Session, user: User):
    """Test no alerts when no budgets set."""
    alerts = check_budget_alerts(db, user)
    assert alerts == []


def test_budget_alert_under_limit(db: Session, user: User, account):
    """Test no alert when under budget."""
    budget = Budget(
        user_id=user.id,
        category="Кафе и кофе",
        monthly_limit=Decimal("5000"),
        currency="RUB",
    )
    db.add(budget)
    db.commit()

    add_expense(db, user.id, Decimal("1000"), "RUB", account.id, category="Кафе и кофе")

    alerts = check_budget_alerts(db, user)
    assert alerts == []


def test_budget_alert_at_80_percent(db: Session, user: User, account):
    """Test warning at 80% of budget."""
    budget = Budget(
        user_id=user.id,
        category="Кафе и кофе",
        monthly_limit=Decimal("5000"),
        currency="RUB",
    )
    db.add(budget)
    db.commit()

    add_expense(db, user.id, Decimal("4100"), "RUB", account.id, category="Кафе и кофе")

    alerts = check_budget_alerts(db, user)
    assert len(alerts) == 1
    assert "⚠️" in alerts[0]


def test_budget_alert_over_limit(db: Session, user: User, account):
    """Test critical alert when over budget."""
    budget = Budget(
        user_id=user.id,
        category="Кафе и кофе",
        monthly_limit=Decimal("3000"),
        currency="RUB",
    )
    db.add(budget)
    db.commit()

    add_expense(db, user.id, Decimal("3500"), "RUB", account.id, category="Кафе и кофе")

    alerts = check_budget_alerts(db, user)
    assert len(alerts) == 1
    assert "🚨" in alerts[0]
    assert "превышен" in alerts[0]


def test_saved_10k_achievement(db: Session, user: User, account):
    """Test saved_10k achievement when net income >= 10000 RUB."""
    from services.ledger import add_income
    add_income(db, user.id, Decimal("20000"), "RUB", account.id, category="Зарплата")

    user.last_activity_date = None
    user.streak_days = 0
    user.total_operations = 0
    user.achievements_json = []
    db.commit()

    result = update_streak(db, user)
    assert "saved_10k" in result["new_achievements"]


# === Edge Case Tests ===

def test_budget_alert_different_currency(db: Session, user: User, account):
    """Test budget in RUB ignores USD expenses."""
    budget = Budget(
        user_id=user.id,
        category="Транспорт",
        monthly_limit=Decimal("1000"),
        currency="RUB",
    )
    db.add(budget)
    db.commit()

    # Add USD expense — should not trigger RUB budget alert
    from services.ledger import add_expense
    usd_account = create_account(db, user.id, "USD Card", "USD", Decimal("10000"))
    add_expense(db, user.id, Decimal("5000"), "USD", usd_account.id, category="Транспорт")

    alerts = check_budget_alerts(db, user)
    assert alerts == []


def test_multiple_budget_alerts(db: Session, user: User, account):
    """Test alerts fire for multiple categories."""
    budget1 = Budget(user_id=user.id, category="Транспорт", monthly_limit=Decimal("100"), currency="RUB")
    budget2 = Budget(user_id=user.id, category="Еда и продукты", monthly_limit=Decimal("100"), currency="RUB")
    db.add_all([budget1, budget2])
    db.commit()

    add_expense(db, user.id, Decimal("200"), "RUB", account.id, category="Транспорт")
    add_expense(db, user.id, Decimal("200"), "RUB", account.id, category="Еда и продукты")

    alerts = check_budget_alerts(db, user)
    assert len(alerts) == 2


def test_streak_exactly_yesterday(db: Session, user: User):
    """Test streak increments when last activity was exactly yesterday."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    user.last_activity_date = yesterday
    user.streak_days = 1
    user.total_operations = 1
    user.achievements_json = ["first_op"]
    db.commit()

    result = update_streak(db, user)
    assert result["streak"] == 2


def test_multiple_achievements_at_once(db: Session, user: User):
    """Test multiple achievements can be unlocked simultaneously."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    user.last_activity_date = yesterday
    user.streak_days = 6  # Will become 7 → streak_7
    user.total_operations = 9  # Will become 10 → ops_10
    user.achievements_json = ["first_op"]
    db.commit()

    result = update_streak(db, user)
    assert "streak_7" in result["new_achievements"]
    assert "ops_10" in result["new_achievements"]


def test_saved_10k_not_triggered_with_expenses(db: Session, user: User, account):
    """Test saved_10k NOT triggered when expenses cancel out income."""
    from services.ledger import add_income
    add_income(db, user.id, Decimal("15000"), "RUB", account.id, category="Зарплата")
    add_expense(db, user.id, Decimal("10000"), "RUB", account.id, category="Еда и продукты")

    user.last_activity_date = None
    user.streak_days = 0
    user.total_operations = 0
    user.achievements_json = []
    db.commit()

    result = update_streak(db, user)
    assert "saved_10k" not in result["new_achievements"]


# === Budget Progress in Reports ===

def test_report_includes_budget_progress(db: Session, user: User, account):
    """Test that get_report includes budget progress data."""
    from services.reports import get_report

    budget = Budget(
        user_id=user.id,
        category="Транспорт",
        monthly_limit=Decimal("5000"),
        currency="RUB",
    )
    db.add(budget)
    db.commit()

    add_expense(db, user.id, Decimal("2000"), "RUB", account.id, category="Транспорт")

    report = get_report(db, user.id, period_preset="month", user_timezone="Europe/Moscow")

    assert "budget_progress" in report
    assert len(report["budget_progress"]) == 1
    bp = report["budget_progress"][0]
    assert bp["category"] == "Транспорт"
    assert bp["spent"] == Decimal("2000")
    assert bp["limit"] == Decimal("5000")
    assert bp["pct"] == 40


def test_report_budget_progress_empty(db: Session, user: User):
    """Test report with no budgets returns empty list."""
    from services.reports import get_report

    report = get_report(db, user.id, period_preset="month", user_timezone="Europe/Moscow")
    assert report["budget_progress"] == []
