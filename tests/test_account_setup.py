"""Tests for bot/account_setup — custom-currency text flow."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from db.models import Account
from db.session import SessionLocal, init_db, engine
from bot.account_setup import (
    _handle_custom_currency_input,
    _handle_custom_currency,
    handle_custom_account_name,
)


@pytest.fixture(scope="function")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()
    from db.models import Base
    Base.metadata.drop_all(bind=engine)


def make_message_update(text, user_id=555):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def make_callback_update(data, user_id=555):
    update = MagicMock()
    query = MagicMock()
    query.data = data
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update.callback_query = query
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    return context


async def test_custom_currency_input_valid_ticker():
    """A valid free-form ticker is uppercased, stored, and the flag is cleared."""
    update = make_message_update("vnd")
    context = make_context()
    context.user_data["awaiting_custom_currency"] = True

    await _handle_custom_currency_input(update, context)

    assert context.user_data["custom_account_currency"] == "VND"
    assert "awaiting_custom_currency" not in context.user_data
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args.args[0]
    assert "VND" in text
    assert "Шаг 2" in text


@pytest.mark.parametrize("bad", ["мбр$", "tooooolongticker"])
async def test_custom_currency_input_invalid_keeps_flag(bad):
    """An invalid ticker keeps the awaiting flag and shows the back-to-currencies button."""
    update = make_message_update(bad)
    context = make_context()
    context.user_data["awaiting_custom_currency"] = True

    await _handle_custom_currency_input(update, context)

    assert context.user_data.get("awaiting_custom_currency") is True
    assert "custom_account_currency" not in context.user_data
    update.message.reply_text.assert_called_once()
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == "↩️ Назад к валютам"


async def test_handle_custom_currency_custom_branch_sets_flag():
    """The acct:currency:custom callback arms awaiting_custom_currency and prompts for a ticker."""
    update = make_callback_update("acct:currency:custom")
    context = make_context()

    await _handle_custom_currency(update, context, "acct:currency:custom")

    assert context.user_data["awaiting_custom_currency"] is True
    update.callback_query.edit_message_text.assert_called_once()
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "тикер" in text.lower()


async def test_full_custom_flow_creates_account_with_arbitrary_ticker(db):
    """custom currency -> ticker -> name -> balance creates an account with the free-form ticker."""
    context = make_context()

    # Step 1: choose the "custom currency" branch
    cb = make_callback_update("acct:currency:custom")
    await _handle_custom_currency(cb, context, "acct:currency:custom")
    assert context.user_data["awaiting_custom_currency"] is True

    # Step 1b: enter the free-form ticker
    await handle_custom_account_name(make_message_update("DONG"), context)
    assert context.user_data["custom_account_currency"] == "DONG"
    assert "awaiting_custom_currency" not in context.user_data

    # Step 2: enter the account name
    await handle_custom_account_name(make_message_update("Вьетнам"), context)
    assert context.user_data["custom_account_name"] == "Вьетнам"

    # Step 3: enter the initial balance -> finalizes account creation
    await handle_custom_account_name(make_message_update("1000000"), context)

    db.expire_all()
    accounts = db.query(Account).all()
    assert len(accounts) == 1
    assert accounts[0].name == "Вьетнам"
    assert accounts[0].currency == "DONG"
    assert accounts[0].balance == Decimal("1000000")
