"""End-to-end pipeline test: 'Создай счёт в VND название Донг'.

parse_message (mocked) -> process_user_text creates a PendingAction ->
handle_confirm creates the Account with the arbitrary VND ticker.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models import Account, PendingAction, ActionType, PendingStatus
from db.session import SessionLocal, init_db, engine
from services.ledger import get_or_create_user
from schemas.llm_schema import LLMResponse, LLMResponseData, AccountNewSchema
from bot.handlers import process_user_text
from bot.callbacks import handle_confirm


@pytest.fixture(scope="function")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()
    from db.models import Base
    Base.metadata.drop_all(bind=engine)


def _llm_response():
    return LLMResponse(
        intent="account_add",
        confidence=0.95,
        data=LLMResponseData(
            account_new=AccountNewSchema(name="Донг", currency="VND", initial_balance=0.0)
        ),
    )


def _msg_update(tg_id: int):
    update = MagicMock()
    update.effective_user.id = tg_id
    sent = MagicMock()
    sent.message_id = 12345
    update.message.reply_text = AsyncMock(return_value=sent)
    return update


def _cb_query(tg_id: int):
    query = MagicMock()
    query.from_user.id = tg_id
    query.message.text = "Создать счёт «Донг» (VND)?"
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()
    return query


async def test_e2e_account_creation_vnd(db):
    tg_id = 424242
    user = get_or_create_user(db, tg_id, "Europe/Moscow")
    db.commit()

    context = MagicMock()
    context.user_data = {}

    with patch("bot.handlers.parse_message", new_callable=AsyncMock) as mock_parse, \
         patch("bot.middleware.check_subscription", new_callable=AsyncMock) as mock_sub:
        mock_parse.return_value = _llm_response()
        mock_sub.return_value = (True, None, None)
        await process_user_text(_msg_update(tg_id), context, "Создай счёт в VND название Донг")

    # process_user_text must have created a pending account_add action.
    db.expire_all()
    pending = db.query(PendingAction).filter(
        PendingAction.user_id == user.id,
        PendingAction.action_type == ActionType.ACCOUNT_ADD,
        PendingAction.status == PendingStatus.PENDING,
    ).first()
    assert pending is not None

    # Confirming it creates the account — no UnboundLocalError on the account_add branch.
    await handle_confirm(db, _cb_query(tg_id), pending.id)

    db.expire_all()
    acc = db.query(Account).filter(Account.name == "Донг").first()
    assert acc is not None
    assert acc.currency == "VND"

    db.refresh(pending)
    assert pending.status == PendingStatus.CONFIRMED
