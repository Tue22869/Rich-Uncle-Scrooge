"""Regression smoke tests for bot/menu module-level helpers and constants."""
from telegram import ReplyKeyboardMarkup

import bot.menu as menu


def test_menu_navigation_constants():
    """Menu callback-data constants keep their stable 'menu:' prefixes."""
    assert menu.MENU_MAIN == "menu:main"
    assert menu.MENU_FINANCES == "menu:finances"
    assert menu.MENU_REPORTS == "menu:reports"
    assert menu.MENU_PREMIUM == "menu:premium"
    assert menu.MENU_HELP == "menu:help"
    assert menu.MENU_SETTINGS == "menu:settings"
    assert menu.MENU_ABOUT == "menu:about"
    assert menu.MENU_STATUS == "menu:status"
    for value in (menu.MENU_MAIN, menu.MENU_FINANCES, menu.MENU_REPORTS,
                  menu.MENU_PREMIUM, menu.MENU_HELP, menu.MENU_SETTINGS,
                  menu.MENU_ABOUT, menu.MENU_STATUS):
        assert value.startswith("menu:")


def test_persistent_menu_is_reply_keyboard():
    """PERSISTENT_MENU is a resizable, persistent ReplyKeyboardMarkup."""
    assert isinstance(menu.PERSISTENT_MENU, ReplyKeyboardMarkup)
    assert menu.PERSISTENT_MENU.resize_keyboard is True
    assert menu.PERSISTENT_MENU.is_persistent is True


def test_persistent_labels_match_keyboard_buttons():
    """Every button on PERSISTENT_MENU has a matching entry in PERSISTENT_LABELS."""
    button_texts = {
        btn.text
        for row in menu.PERSISTENT_MENU.keyboard
        for btn in row
    }
    assert button_texts == menu.PERSISTENT_LABELS
