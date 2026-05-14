"""Tests for bot/menu account-management callback dispatch and rename input."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.menu as menu
from bot.menu import account_management_callback, handle_rename_account_input
from db.models import Account
from db.session import SessionLocal, init_db, engine
from services.ledger import get_or_create_user, create_account


@pytest.fixture(scope="function")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()
    from db.models import Base
    Base.metadata.drop_all(bind=engine)


def make_callback_update(data, user_id=777):
    update = MagicMock()
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def make_message_update(text, user_id=777):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    return context


# === account_management_callback routing ===

async def test_routes_setmain():
    update = make_callback_update("acct:setmain:42")
    context = make_context()
    with patch.object(menu, "_handle_setmain_callback", new_callable=AsyncMock) as m:
        await account_management_callback(update, context)
    m.assert_awaited_once_with(update, 42)
    update.callback_query.answer.assert_awaited()


async def test_routes_rename():
    update = make_callback_update("acct:rename:42")
    context = make_context()
    with patch.object(menu, "_handle_rename_start", new_callable=AsyncMock) as m:
        await account_management_callback(update, context)
    m.assert_awaited_once_with(update, context, 42)


async def test_routes_delete():
    update = make_callback_update("acct:delete:42")
    context = make_context()
    with patch.object(menu, "_handle_delete_prompt", new_callable=AsyncMock) as m:
        await account_management_callback(update, context)
    m.assert_awaited_once_with(update, 42)


async def test_routes_delete_confirm():
    update = make_callback_update("acct:delete_confirm:42")
    context = make_context()
    with patch.object(menu, "_handle_delete_confirm", new_callable=AsyncMock) as m:
        await account_management_callback(update, context)
    m.assert_awaited_once_with(update, 42)


async def test_routes_rename_cancel():
    update = make_callback_update("acct:rename_cancel")
    context = make_context()
    with patch.object(menu, "_handle_rename_cancel", new_callable=AsyncMock) as m:
        await account_management_callback(update, context)
    m.assert_awaited_once_with(update, context)


async def test_broken_payload_answers_and_does_not_crash():
    """acct:setmain: with no id — query.answer is called and nothing raises."""
    update = make_callback_update("acct:setmain:")
    context = make_context()
    await account_management_callback(update, context)
    update.callback_query.answer.assert_awaited()


# === handle_rename_account_input validation ===

async def test_rename_input_too_long_keeps_state(db):
    user = get_or_create_user(db, 777, "Europe/Moscow")
    acc = create_account(db, user.id, "Старое", "RUB", Decimal("0"))
    context = make_context()
    context.user_data["rename_account_id"] = acc.id

    await handle_rename_account_input(make_message_update("x" * 51), context)

    update_reply = context.user_data.get("rename_account_id")
    assert update_reply == acc.id  # state retained for a retry


async def test_rename_input_duplicate_name_errors(db):
    user = get_or_create_user(db, 777, "Europe/Moscow")
    acc = create_account(db, user.id, "Старое", "RUB", Decimal("0"))
    create_account(db, user.id, "Занятое", "RUB", Decimal("0"))
    context = make_context()
    context.user_data["rename_account_id"] = acc.id
    update = make_message_update("Занятое")

    await handle_rename_account_input(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "уже существует" in update.message.reply_text.call_args.args[0]


async def test_rename_input_success_renames_and_shows_screen(db):
    user = get_or_create_user(db, 777, "Europe/Moscow")
    acc = create_account(db, user.id, "Старое", "RUB", Decimal("0"))
    context = make_context()
    context.user_data["rename_account_id"] = acc.id
    update = make_message_update("Новое имя")

    with patch.object(menu, "_show_accounts_screen", new_callable=AsyncMock) as show:
        await handle_rename_account_input(update, context)

    show.assert_awaited_once()
    assert "rename_account_id" not in context.user_data
    db.expire_all()
    renamed = db.query(Account).filter(Account.id == acc.id).first()
    assert renamed.name == "Новое имя"
