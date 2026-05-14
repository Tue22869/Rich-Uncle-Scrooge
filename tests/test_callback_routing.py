"""Tests for bot/handlers.callback_handler acct: prefix routing."""
from unittest.mock import AsyncMock, MagicMock, patch

import bot.menu as menu
import bot.account_setup as account_setup
from bot.handlers import callback_handler


def make_update(data):
    update = MagicMock()
    query = MagicMock()
    query.data = data
    update.callback_query = query
    return update


async def test_routes_setmain_to_account_management():
    update = make_update("acct:setmain:1")
    context = MagicMock()
    with patch.object(menu, "account_management_callback", new_callable=AsyncMock) as m, \
         patch.object(account_setup, "account_setup_callback", new_callable=AsyncMock) as s:
        await callback_handler(update, context)
    m.assert_awaited_once_with(update, context)
    s.assert_not_awaited()


async def test_routes_delete_confirm_to_account_management():
    update = make_update("acct:delete_confirm:1")
    context = MagicMock()
    with patch.object(menu, "account_management_callback", new_callable=AsyncMock) as m:
        await callback_handler(update, context)
    m.assert_awaited_once_with(update, context)


async def test_routes_custom_to_account_setup_not_management():
    update = make_update("acct:custom")
    context = MagicMock()
    with patch.object(account_setup, "account_setup_callback", new_callable=AsyncMock) as s, \
         patch.object(menu, "account_management_callback", new_callable=AsyncMock) as m:
        await callback_handler(update, context)
    s.assert_awaited_once_with(update, context)
    m.assert_not_awaited()


async def test_routes_more_to_account_action():
    update = make_update("acct:more")
    context = MagicMock()
    with patch.object(account_setup, "account_action_callback", new_callable=AsyncMock) as a:
        await callback_handler(update, context)
    a.assert_awaited_once_with(update, context)
