"""Tests for ledger operations."""
import pytest
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session

from db.models import User, Account, Transaction, TransactionType
from db.session import SessionLocal, init_db, engine
from services.ledger import (
    get_or_create_user, add_income, add_expense, transfer,
    create_account, delete_account, rename_account, set_default_account,
    find_account_by_name, update_transaction, delete_transaction_by_id,
    list_user_transactions, get_transaction_by_row_number
)


@pytest.fixture(scope="function")
def db():
    """Create test database session."""
    init_db()
    db = SessionLocal()
    yield db
    db.close()
    from db.models import Base
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def user(db: Session):
    """Create test user."""
    return get_or_create_user(db, 12345, "Europe/Moscow")


@pytest.fixture
def account(db: Session, user: User):
    """Create test account with 1000 RUB."""
    return create_account(db, user.id, "Основной", "RUB", Decimal("1000.00"))


# === User Tests ===

def test_get_or_create_user_new(db: Session):
    """Test creating new user."""
    user = get_or_create_user(db, 11111)
    assert user.tg_user_id == 11111
    assert user.timezone == "Europe/London"


def test_get_or_create_user_existing(db: Session):
    """Test getting existing user."""
    user1 = get_or_create_user(db, 11111)
    user2 = get_or_create_user(db, 11111)
    assert user1.id == user2.id


def test_get_or_create_user_with_timezone(db: Session):
    """Test creating user with custom timezone."""
    user = get_or_create_user(db, 22222, "Europe/Moscow")
    assert user.timezone == "Europe/Moscow"


# === Account Tests ===

def test_create_account_basic(db: Session, user: User):
    """Test basic account creation."""
    acc = create_account(db, user.id, "Наличка", "RUB")
    assert acc.name == "Наличка"
    assert acc.currency == "RUB"
    assert acc.balance == Decimal("0.00")


def test_create_account_with_balance(db: Session, user: User):
    """Test account creation with initial balance."""
    acc = create_account(db, user.id, "Карта", "USD", Decimal("500.00"))
    assert acc.balance == Decimal("500.00")


def test_find_account_exact(db: Session, user: User, account: Account):
    """Test finding account by exact name."""
    found = find_account_by_name(db, user.id, "Основной")
    assert found is not None
    assert found.id == account.id


def test_find_account_fuzzy(db: Session, user: User, account: Account):
    """Test finding account by partial name."""
    found = find_account_by_name(db, user.id, "основ")
    assert found is not None
    assert found.id == account.id


def test_find_account_not_found(db: Session, user: User):
    """Test account not found."""
    found = find_account_by_name(db, user.id, "Несуществующий")
    assert found is None


def test_delete_account_zero_balance(db: Session, user: User):
    """Test deleting account with zero balance."""
    acc = create_account(db, user.id, "Для удаления", "RUB", Decimal("0.00"))
    result = delete_account(db, user.id, acc.id)
    assert result is True


def test_delete_account_non_zero_balance(db: Session, user: User, account: Account):
    """Test deleting account with non-zero balance fails."""
    with pytest.raises(ValueError, match="Cannot delete account"):
        delete_account(db, user.id, account.id)


def test_rename_account(db: Session, user: User, account: Account):
    """Test renaming account."""
    renamed = rename_account(db, user.id, account.id, "Новое имя")
    assert renamed.name == "Новое имя"


def test_set_default_account(db: Session, user: User, account: Account):
    """Test setting default account."""
    result = set_default_account(db, user.id, account.id)
    assert result.is_default is True
    
    db.refresh(user)
    assert user.default_account_id == account.id


# === Income Tests ===

def test_add_income_basic(db: Session, user: User, account: Account):
    """Test adding basic income."""
    initial = account.balance
    tx = add_income(db, user.id, Decimal("500.00"), "RUB", account.id)
    
    db.refresh(account)
    assert account.balance == initial + Decimal("500.00")
    assert tx.type == TransactionType.INCOME
    assert tx.amount == Decimal("500.00")


def test_add_income_with_category(db: Session, user: User, account: Account):
    """Test adding income with category."""
    tx = add_income(
        db, user.id, Decimal("50000.00"), "RUB", account.id,
        category="Зарплата", subcategory="оклад", description="Январь"
    )
    
    assert tx.category == "Зарплата"
    assert tx.subcategory == "оклад"
    assert tx.description == "Январь"


# === Expense Tests ===

def test_add_expense_basic(db: Session, user: User, account: Account):
    """Test adding basic expense."""
    initial = account.balance
    tx = add_expense(db, user.id, Decimal("100.00"), "RUB", account.id)
    
    db.refresh(account)
    assert account.balance == initial - Decimal("100.00")
    assert tx.type == TransactionType.EXPENSE


def test_add_expense_with_category(db: Session, user: User, account: Account):
    """Test adding expense with category."""
    tx = add_expense(
        db, user.id, Decimal("320.00"), "RUB", account.id,
        category="Кафе и кофе", subcategory="кофе", description="Старбакс"
    )
    
    assert tx.category == "Кафе и кофе"
    assert tx.subcategory == "кофе"


def test_add_expense_insufficient_balance(db: Session, user: User, account: Account):
    """Test expense fails with insufficient balance."""
    with pytest.raises(ValueError, match="Insufficient balance"):
        add_expense(db, user.id, Decimal("5000.00"), "RUB", account.id)


# === Transfer Tests ===

def test_transfer_same_currency(db: Session, user: User, account: Account):
    """Test transfer between same currency accounts."""
    acc2 = create_account(db, user.id, "Наличка", "RUB", Decimal("0.00"))
    
    tx = transfer(db, user.id, Decimal("300.00"), "RUB", account.id, acc2.id)
    
    db.refresh(account)
    db.refresh(acc2)
    
    assert account.balance == Decimal("700.00")
    assert acc2.balance == Decimal("300.00")
    assert tx.type == TransactionType.TRANSFER


def test_transfer_cross_currency(db: Session, user: User, account: Account):
    """Test cross-currency transfer."""
    usd_acc = create_account(db, user.id, "USD Account", "USD", Decimal("0.00"))
    
    tx = transfer(
        db, user.id, Decimal("900.00"), "RUB", account.id, usd_acc.id,
        to_amount=Decimal("10.00"), to_currency="USD"
    )
    
    db.refresh(account)
    db.refresh(usd_acc)
    
    assert account.balance == Decimal("100.00")
    assert usd_acc.balance == Decimal("10.00")


def test_transfer_insufficient_balance(db: Session, user: User, account: Account):
    """Test transfer fails with insufficient balance."""
    acc2 = create_account(db, user.id, "Другой", "RUB")
    
    with pytest.raises(ValueError, match="Insufficient balance"):
        transfer(db, user.id, Decimal("5000.00"), "RUB", account.id, acc2.id)


# === Transaction Management Tests ===

def test_list_transactions(db: Session, user: User, account: Account):
    """Test listing transactions."""
    add_expense(db, user.id, Decimal("100.00"), "RUB", account.id)
    add_expense(db, user.id, Decimal("200.00"), "RUB", account.id)
    add_income(db, user.id, Decimal("500.00"), "RUB", account.id)
    
    transactions = list_user_transactions(db, user.id)
    
    assert len(transactions) == 3
    # Should be sorted by date desc, numbered 1, 2, 3
    assert transactions[0][0] == 1
    assert transactions[1][0] == 2
    assert transactions[2][0] == 3


def test_get_transaction_by_row_number(db: Session, user: User, account: Account):
    """Test getting transaction by row number."""
    add_expense(db, user.id, Decimal("100.00"), "RUB", account.id, description="Первый")
    add_expense(db, user.id, Decimal("200.00"), "RUB", account.id, description="Второй")
    
    tx = get_transaction_by_row_number(db, user.id, 1)  # Most recent
    assert tx is not None
    assert tx.description == "Второй"


def test_update_transaction_amount(db: Session, user: User, account: Account):
    """Test updating transaction amount."""
    tx = add_expense(db, user.id, Decimal("100.00"), "RUB", account.id)
    initial_balance = account.balance  # 900
    
    updated = update_transaction(db, user.id, tx.id, new_amount=Decimal("150.00"))
    
    db.refresh(account)
    assert updated.amount == Decimal("150.00")
    # Balance should decrease by additional 50 (expense increased)
    assert account.balance == initial_balance - Decimal("50.00")


def test_update_transaction_category(db: Session, user: User, account: Account):
    """Test updating transaction category."""
    tx = add_expense(db, user.id, Decimal("100.00"), "RUB", account.id)
    
    updated = update_transaction(db, user.id, tx.id, new_category="Транспорт")
    
    assert updated.category == "Транспорт"


def test_delete_transaction_expense(db: Session, user: User, account: Account):
    """Test deleting expense transaction reverses balance."""
    tx = add_expense(db, user.id, Decimal("100.00"), "RUB", account.id)
    balance_after_expense = account.balance  # 900
    
    delete_transaction_by_id(db, user.id, tx.id)
    
    db.refresh(account)
    # Balance should be restored
    assert account.balance == balance_after_expense + Decimal("100.00")


def test_delete_transaction_income(db: Session, user: User, account: Account):
    """Test deleting income transaction reverses balance."""
    tx = add_income(db, user.id, Decimal("500.00"), "RUB", account.id)
    balance_after_income = account.balance  # 1500
    
    delete_transaction_by_id(db, user.id, tx.id)
    
    db.refresh(account)
    # Balance should be restored
    assert account.balance == balance_after_income - Decimal("500.00")
