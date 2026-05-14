"""Tests for bot/menu._show_sheets_screen."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.menu import _show_sheets_screen
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


def make_callback_update(user_id=888):
    update = MagicMock()
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()
    update.callback_query = query
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _flatten(markup):
    return [btn for row in markup.inline_keyboard for btn in row]


async def test_sheets_screen_configured_with_binding(db):
    """Configured + a linked spreadsheet -> 4 action buttons (export/import/reset/main)."""
    user = get_or_create_user(db, 888, "Europe/Moscow")
    user.google_sheets_spreadsheet_id = "SHEET123"
    db.commit()

    update = make_callback_update(888)
    with patch("services.google_sheets_client.is_configured", return_value=True), \
         patch("services.google_sheets_client.get_service_account_email", return_value="sa@example.com"):
        await _show_sheets_screen(update)

    update.callback_query.edit_message_text.assert_awaited_once()
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    buttons = _flatten(markup)
    assert len(buttons) == 4
    callbacks = {b.callback_data for b in buttons}
    assert {"cmd:sheets:export", "cmd:sheets:import", "cmd:sheets:reset"} <= callbacks


async def test_sheets_screen_configured_without_binding(db):
    """Configured but no linked spreadsheet -> setup instructions + only the main-menu button."""
    get_or_create_user(db, 888, "Europe/Moscow")

    update = make_callback_update(888)
    with patch("services.google_sheets_client.is_configured", return_value=True), \
         patch("services.google_sheets_client.get_service_account_email", return_value="sa@example.com"):
        await _show_sheets_screen(update)

    call = update.callback_query.edit_message_text.call_args
    text = call.args[0]
    assert "Подключение Google Sheets" in text
    assert "sa@example.com" in text
    buttons = _flatten(call.kwargs["reply_markup"])
    assert len(buttons) == 1


async def test_sheets_screen_not_configured(db):
    """is_configured() False -> the 'not configured' message."""
    get_or_create_user(db, 888, "Europe/Moscow")

    update = make_callback_update(888)
    with patch("services.google_sheets_client.is_configured", return_value=False):
        await _show_sheets_screen(update)

    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "не настроена" in text
