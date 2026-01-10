"""Date utilities."""
from datetime import datetime, timedelta
from typing import Optional, Tuple
import pytz


def get_user_timezone(timezone_str: str = "Europe/London") -> pytz.BaseTzInfo:
    """Get timezone object from string."""
    try:
        return pytz.timezone(timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        return pytz.timezone("Europe/London")


def now_in_timezone(timezone_str: str = "Europe/London") -> datetime:
    """Get current datetime in user timezone."""
    tz = get_user_timezone(timezone_str)
    return datetime.now(tz)


def parse_period(
    period_preset: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    user_timezone: str = "Europe/London"
) -> Tuple[datetime, datetime]:
    """
    Parse period from preset or dates.
    Returns (start, end) datetime in user timezone.
    """
    tz = get_user_timezone(user_timezone)
    now = datetime.now(tz)

    if period_preset == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period_preset == "week":
        # Current week (Monday to Sunday)
        days_since_monday = now.weekday()
        start = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period_preset == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(microseconds=1)
        else:
            end = now.replace(month=now.month + 1, day=1) - timedelta(microseconds=1)
    elif period_preset == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(microseconds=1)
    elif from_date and to_date:
        # Custom period
        try:
            start = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
            if not start.tzinfo:
                start = tz.localize(start)
            end = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
            if not end.tzinfo:
                end = tz.localize(end)
            # Set to end of day
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        except (ValueError, AttributeError):
            # Fallback to today
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        # Default to today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    return start, end


def get_prev_period(
    period_preset: str,
    user_timezone: str = "Europe/London"
) -> Tuple[datetime, datetime]:
    """Get previous period for comparison."""
    tz = get_user_timezone(user_timezone)
    now = datetime.now(tz)

    if period_preset == "today":
        prev_date = now - timedelta(days=1)
        start = prev_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = prev_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period_preset == "week":
        days_since_monday = now.weekday()
        current_week_start = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        prev_week_start = current_week_start - timedelta(days=7)
        start = prev_week_start
        end = (prev_week_start + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif period_preset == "month":
        if now.month == 1:
            prev_month = 12
            prev_year = now.year - 1
        else:
            prev_month = now.month - 1
            prev_year = now.year
        start = now.replace(year=prev_year, month=prev_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        if prev_month == 12:
            end = now.replace(year=prev_year + 1, month=1, day=1) - timedelta(microseconds=1)
        else:
            end = now.replace(year=prev_year, month=prev_month + 1, day=1) - timedelta(microseconds=1)
    elif period_preset == "year":
        start = now.replace(year=now.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(year=now.year, month=1, day=1) - timedelta(microseconds=1)
    else:
        # Default to previous day
        prev_date = now - timedelta(days=1)
        start = prev_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = prev_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    return start, end


def format_datetime(dt: datetime, format_str: str = "%d.%m.%Y %H:%M") -> str:
    """Format datetime to string."""
    return dt.strftime(format_str)


def format_date(dt, format_str: str = "%d.%m.%Y") -> str:
    """Format date to string. Accepts datetime or string."""
    if isinstance(dt, str):
        # Try to parse the string
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt  # Return as-is if parsing fails
    return dt.strftime(format_str)


def format_operation_date(date_str: str) -> str:
    """Format operation date for user display. E.g. '18.12, 19:54' or 'сегодня'."""
    if not date_str:
        return "сегодня"
    
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        
        # Check if today
        if dt.date() == now.date():
            return f"сегодня, {dt.strftime('%H:%M')}"
        # Check if yesterday
        elif dt.date() == (now.date() - timedelta(days=1)):
            return f"вчера, {dt.strftime('%H:%M')}"
        # Otherwise show date
        else:
            return dt.strftime("%d.%m, %H:%M")
    except (ValueError, AttributeError):
        return date_str

