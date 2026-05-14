"""Tests for bot.menu._build_accounts_screen — pure builder, no mocks needed."""
from decimal import Decimal
from types import SimpleNamespace

from telegram import InlineKeyboardMarkup

from bot.menu import _build_accounts_screen


def _acc(id, name, currency, balance, is_default):
    return SimpleNamespace(
        id=id, name=name, currency=currency,
        balance=Decimal(balance), is_default=is_default,
    )


def _all_callbacks(keyboard: InlineKeyboardMarkup) -> list[str]:
    return [btn.callback_data for row in keyboard.inline_keyboard for btn in row]


def test_empty_list_shows_placeholder_and_create_button():
    text, keyboard = _build_accounts_screen([])

    assert "пусто" in text.lower()
    callbacks = _all_callbacks(keyboard)
    assert "acct:custom" in callbacks
    assert "menu:main" in callbacks or any("main" in c for c in callbacks)


def test_single_default_account_has_star_in_text_no_star_button():
    acc = _acc(1, "Карта", "RUB", "10000", is_default=True)
    text, keyboard = _build_accounts_screen([acc])

    assert "⭐ Карта (RUB)" in text
    callbacks = _all_callbacks(keyboard)
    # default account: no setmain button, but rename + delete present
    assert "acct:setmain:1" not in callbacks
    assert "acct:rename:1" in callbacks
    assert "acct:delete:1" in callbacks


def test_non_default_account_gets_setmain_button():
    main = _acc(1, "Карта", "RUB", "10000", is_default=True)
    other = _acc(2, "USD", "USD", "0", is_default=False)
    text, keyboard = _build_accounts_screen([main, other])

    callbacks = _all_callbacks(keyboard)
    assert "acct:setmain:2" in callbacks
    assert "acct:setmain:1" not in callbacks
    assert "acct:rename:2" in callbacks
    assert "acct:delete:2" in callbacks
    assert "• USD (USD)" in text


def test_non_empty_screen_always_has_footer_buttons():
    acc = _acc(1, "Карта", "RUB", "10000", is_default=True)
    _, keyboard = _build_accounts_screen([acc])

    callbacks = _all_callbacks(keyboard)
    assert "cmd:sheets" in callbacks
    assert "cmd:history" in callbacks
    assert "acct:custom" in callbacks
    assert any("main" in c for c in callbacks)
