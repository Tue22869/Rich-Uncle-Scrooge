"""Tests for utility functions."""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from utils.dates import (
    format_date, format_operation_date, parse_period,
    get_user_timezone, now_in_timezone
)
from utils.money import format_amount


# === Money Formatting Tests ===

def test_format_amount_rub():
    """Test formatting RUB amount."""
    result = format_amount(Decimal("1234.56"), "RUB")
    assert "1 234,56" in result or "1234,56" in result


def test_format_amount_usd():
    """Test formatting USD amount."""
    result = format_amount(Decimal("100.00"), "USD")
    assert "100" in result


def test_format_amount_zero():
    """Test formatting zero amount."""
    result = format_amount(Decimal("0.00"), "RUB")
    assert "0" in result


def test_format_amount_large():
    """Test formatting large amount."""
    result = format_amount(Decimal("1000000.00"), "RUB")
    assert "000" in result


# === Date Formatting Tests ===

def test_format_date_datetime():
    """Test formatting datetime object."""
    dt = datetime(2025, 12, 18)
    result = format_date(dt)
    assert result == "18.12.2025"


def test_format_date_string():
    """Test formatting date string."""
    result = format_date("2025-12-18T19:00:00+00:00")
    assert result == "18.12.2025"


def test_format_date_custom_format():
    """Test formatting with custom format."""
    dt = datetime(2025, 12, 18)
    result = format_date(dt, "%Y-%m-%d")
    assert result == "2025-12-18"


def test_format_operation_date_today():
    """Test formatting operation date for today."""
    now = datetime.now()
    now_str = now.isoformat()
    result = format_operation_date(now_str)
    assert "сегодня" in result


def test_format_operation_date_none():
    """Test formatting None date."""
    result = format_operation_date(None)
    assert result == "сегодня"


def test_format_operation_date_other():
    """Test formatting date from past."""
    past = datetime(2025, 1, 15, 14, 30)
    result = format_operation_date(past.isoformat())
    assert "15.01" in result


# === Timezone Tests ===

def test_get_user_timezone_valid():
    """Test getting valid timezone."""
    tz = get_user_timezone("Europe/Moscow")
    assert tz is not None


def test_get_user_timezone_invalid():
    """Test getting invalid timezone falls back to London."""
    tz = get_user_timezone("Invalid/Timezone")
    assert str(tz) == "Europe/London"


def test_now_in_timezone():
    """Test getting current time in timezone."""
    now_london = now_in_timezone("Europe/London")
    now_moscow = now_in_timezone("Europe/Moscow")
    # Moscow is ahead of London
    assert now_moscow >= now_london


# === Period Parsing Tests ===

def test_parse_period_today():
    """Test parsing today period."""
    start, end = parse_period("today", None, None, "Europe/London")
    assert start.hour == 0
    assert start.minute == 0
    assert end.hour == 23


def test_parse_period_month():
    """Test parsing month period."""
    start, end = parse_period("month", None, None, "Europe/London")
    assert start.day == 1


def test_parse_period_custom():
    """Test parsing custom period."""
    start, end = parse_period("custom", "2025-12-01", "2025-12-31", "Europe/London")
    assert start.day == 1
    assert end.day == 31

