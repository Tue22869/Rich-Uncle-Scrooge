"""Tests for the new account UX: custom currency, onboarding, transaction count."""
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from db.models import User, Transaction
from db.session import SessionLocal, init_db, engine
from services.ledger import (
    get_or_create_user, create_account, delete_account,
    add_income, add_expense, transfer, count_account_transactions, set_default_account,
)
from services.onboarding import (
    advance_after_first_account,
    advance_after_confirmed_op,
    TIP_STEP_1, TIP_STEP_2, TIP_STEP_3,
)


@pytest.fixture(scope="function")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()
    from db.models import Base
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def user(db: Session):
    return get_or_create_user(db, 98765, "Europe/Moscow")


def test_create_account_with_custom_currency_ticker(db: Session, user: User):
    """create_account does not validate ticker — any non-empty string works."""
    acc = create_account(db, user.id, "Донг", "VND", Decimal("1000000"))
    assert acc.currency == "VND"
    assert acc.balance == Decimal("1000000")


def test_count_account_transactions_includes_transfers(db: Session, user: User):
    rub = create_account(db, user.id, "Карта", "RUB", Decimal("10000"))
    usd = create_account(db, user.id, "USD", "USD", Decimal("0"))

    add_expense(db, user.id, Decimal("100"), "RUB", rub.id)
    add_expense(db, user.id, Decimal("200"), "RUB", rub.id)
    transfer(db, user.id, Decimal("500"), "RUB", rub.id, usd.id,
             to_amount=Decimal("5"), to_currency="USD")

    # rub has 2 expenses + 1 transfer-from = 3
    assert count_account_transactions(db, user.id, rub.id) == 3
    # usd has 1 transfer-to = 1
    assert count_account_transactions(db, user.id, usd.id) == 1


def test_count_account_transactions_empty(db: Session, user: User):
    """An account with no operations counts zero."""
    acc = create_account(db, user.id, "Пустой", "RUB", Decimal("0"))
    assert count_account_transactions(db, user.id, acc.id) == 0


def test_count_account_transactions_income_and_expense(db: Session, user: User):
    """One income + one expense on the same account counts as 2."""
    acc = create_account(db, user.id, "Карта", "RUB", Decimal("1000"))
    add_income(db, user.id, Decimal("500"), "RUB", acc.id)
    add_expense(db, user.id, Decimal("300"), "RUB", acc.id)
    assert count_account_transactions(db, user.id, acc.id) == 2


def test_count_account_transactions_from_and_to_counted_once_each(db: Session, user: User):
    """An account referenced as from in one transfer and to in another counts each row once."""
    a = create_account(db, user.id, "A", "RUB", Decimal("1000"))
    b = create_account(db, user.id, "B", "RUB", Decimal("1000"))

    transfer(db, user.id, Decimal("100"), "RUB", a.id, b.id)  # a is from
    transfer(db, user.id, Decimal("200"), "RUB", b.id, a.id)  # a is to

    # Two distinct transfer rows touch account a — counted once each.
    assert count_account_transactions(db, user.id, a.id) == 2
    assert count_account_transactions(db, user.id, b.id) == 2


def test_delete_account_used_in_transfer_removes_transfer(db: Session, user: User):
    """Transfers referencing the deleted account are removed too."""
    rub = create_account(db, user.id, "Карта", "RUB", Decimal("10000"))
    usd = create_account(db, user.id, "USD", "USD", Decimal("0"))
    transfer(db, user.id, Decimal("500"), "RUB", rub.id, usd.id,
             to_amount=Decimal("5"), to_currency="USD")

    delete_account(db, user.id, usd.id)

    # The transfer transaction is gone
    assert db.query(Transaction).filter(Transaction.user_id == user.id).count() == 0


def test_delete_default_reassigns_to_oldest_remaining(db: Session, user: User):
    first = create_account(db, user.id, "Первый", "RUB", Decimal("0"))
    second = create_account(db, user.id, "Второй", "RUB", Decimal("0"))
    create_account(db, user.id, "Третий", "USD", Decimal("0"))

    # First is auto-default
    db.refresh(user)
    assert user.default_account_id == first.id

    delete_account(db, user.id, first.id)
    db.refresh(user)
    # Lowest-id remaining wins
    assert user.default_account_id == second.id


def test_set_default_account_switches_flags(db: Session, user: User):
    a = create_account(db, user.id, "A", "RUB", Decimal("0"))
    b = create_account(db, user.id, "B", "USD", Decimal("0"))

    set_default_account(db, user.id, b.id)

    db.refresh(a)
    db.refresh(b)
    db.refresh(user)
    assert b.is_default is True
    assert a.is_default is False
    assert user.default_account_id == b.id


def test_onboarding_first_account_then_two_ops(db: Session, user: User):
    """Full happy path: 0 -> 1 -> 2 -> 3, then no more tips."""
    assert user.onboarding_step == 0

    tip1 = advance_after_first_account(db, user)
    assert tip1 == TIP_STEP_1
    assert user.onboarding_step == 1

    # Calling again at step 1 doesn't double-fire the first-account tip
    assert advance_after_first_account(db, user) is None
    assert user.onboarding_step == 1

    tip2 = advance_after_confirmed_op(db, user)
    assert tip2 == TIP_STEP_2
    assert user.onboarding_step == 2

    tip3 = advance_after_confirmed_op(db, user)
    assert tip3 == TIP_STEP_3
    assert user.onboarding_step == 3

    # Tour is over
    assert advance_after_confirmed_op(db, user) is None
    assert user.onboarding_step == 3


def test_advance_after_first_account_no_reset_at_higher_steps(db: Session, user: User):
    """At step >= 1 the first-account tip never re-fires and the step is untouched."""
    user.onboarding_step = 2
    db.commit()
    assert advance_after_first_account(db, user) is None
    assert user.onboarding_step == 2

    user.onboarding_step = 3
    db.commit()
    assert advance_after_first_account(db, user) is None
    assert user.onboarding_step == 3


def test_advance_after_confirmed_op_idempotent_at_step_3(db: Session, user: User):
    """Once the tour is complete (step 3), confirmed ops are a no-op forever."""
    user.onboarding_step = 3
    db.commit()
    assert advance_after_confirmed_op(db, user) is None
    assert advance_after_confirmed_op(db, user) is None
    assert user.onboarding_step == 3


def test_onboarding_concurrent_calls_do_not_skip_step(db: Session, user: User):
    """Each advance call moves the step by exactly one — racing calls never skip."""
    # Two near-simultaneous "first account" calls: only one fires the tip.
    tip_a = advance_after_first_account(db, user)
    tip_b = advance_after_first_account(db, user)
    assert {tip_a, tip_b} == {TIP_STEP_1, None}
    assert user.onboarding_step == 1

    # Two racing confirmed-op calls: linear sequence, each advances one step, never two.
    tip_c = advance_after_confirmed_op(db, user)
    tip_d = advance_after_confirmed_op(db, user)
    assert tip_c == TIP_STEP_2
    assert tip_d == TIP_STEP_3
    assert user.onboarding_step == 3
