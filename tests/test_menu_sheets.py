"""Tests for bot.menu._show_sheets_screen — Google Sheets status screen."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.menu import _show_sheets_screen, MENU_MAIN
from db.session import SessionLocal, init_db, engine
from services.ledger import get_or_create_user


@pytest.fixture(scope="function")
def db():
    init_db()
    session = SessionLocal()
    yield session
    session.close()
    from db.models import Base
    Base.metadata.drop_all(bind=engine)


def _cb_update(tg_id: int = 909090):
    update = MagicMock()
    update.effective_user.id = tg_id
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def _callbacks(call_args):
    keyboard = call_args.kwargs["reply_markup"]
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


async def test_configured_with_binding_shows_four_action_buttons(db):
    user = get_or_create_user(db, 909090, "Europe/Moscow")
    user.google_sheets_spreadsheet_id = "sheet-abc-123"
    db.commit()

    update = _cb_update()
    with patch("services.google_sheets_client.is_configured", return_value=True), \
         patch("services.google_sheets_client.get_service_account_email", return_value="bot@svc.iam"):
        await _show_sheets_screen(update)

    update.callback_query.edit_message_text.assert_awaited_once()
    callbacks = _callbacks(update.callback_query.edit_message_text.call_args)
    assert "cmd:sheets:export" in callbacks
    assert "cmd:sheets:import" in callbacks
    assert "cmd:sheets:reset" in callbacks
    assert MENU_MAIN in callbacks


async def test_configured_without_binding_shows_setup_instructions(db):
    get_or_create_user(db, 909090, "Europe/Moscow")

    update = _cb_update()
    with patch("services.google_sheets_client.is_configured", return_value=True), \
         patch("services.google_sheets_client.get_service_account_email", return_value="bot@svc.iam"):
        await _show_sheets_screen(update)

    update.callback_query.edit_message_text.assert_awaited_once()
    call_args = update.callback_query.edit_message_text.call_args
    text = call_args.args[0]
    assert "/sheets" in text
    assert "bot@svc.iam" in text
    callbacks = _callbacks(call_args)
    assert callbacks == [MENU_MAIN]


async def test_not_configured_shows_disabled_message(db):
    get_or_create_user(db, 909090, "Europe/Moscow")

    update = _cb_update()
    with patch("services.google_sheets_client.is_configured", return_value=False):
        await _show_sheets_screen(update)

    update.callback_query.edit_message_text.assert_awaited_once()
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "не настроена" in text.lower()
