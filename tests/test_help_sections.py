"""Tests for bot.handlers.HELP_SECTIONS — content of the sectioned /help."""
from bot.handlers import HELP_SECTIONS


EXPECTED_KEYS = {"overview", "accounts", "record", "reports", "sheets", "troubleshoot"}


def test_all_six_sections_present_and_non_empty():
    assert set(HELP_SECTIONS) == EXPECTED_KEYS
    for key, text in HELP_SECTIONS.items():
        assert isinstance(text, str) and text.strip(), f"section {key} is empty"


def test_accounts_section_mentions_main_account_and_currency():
    accounts = HELP_SECTIONS["accounts"].lower()
    assert "главн" in accounts
    assert "валют" in accounts


def test_record_section_describes_expense_and_income_actions():
    record = HELP_SECTIONS["record"]
    assert "*Расход:*" in record
    assert "*Доход:*" in record
    # Expense is the first concrete action described, before income.
    assert record.index("*Расход:*") < record.index("*Доход:*")


def test_troubleshoot_section_mentions_deletion_and_subscription():
    troubleshoot = HELP_SECTIONS["troubleshoot"].lower()
    assert "удал" in troubleshoot
    assert "подписк" in troubleshoot
