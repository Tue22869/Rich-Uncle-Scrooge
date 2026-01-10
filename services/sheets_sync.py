"""Sync SmartFinances data to user-provided Google Spreadsheets (multi-sheet design).

Design:
- Sheet "Балансы": account balances
- Sheet "YYYY-MM" (e.g. "2026-01"): transactions for that month (last 12 months)
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from db.models import User
from services.google_sheets_client import (
    ensure_sheet,
    clear_and_update_values,
    get_spreadsheet_url,
    get_sheets_service,
    get_all_sheet_titles,
    delete_sheet_by_title,
)
from services.sheets_export import (
    build_balances_sheet_title,
    build_balances_export,
    build_month_sheet_title,
    build_month_transactions_export,
    get_user_transaction_months,
)
from services.sheets_format import format_balances_sheet, format_month_sheet

logger = logging.getLogger(__name__)


def sync_user_to_sheets(db: Session, user_id: int, spreadsheet_id: str) -> str:
    """Sync user data to multiple sheets with formatting. Returns spreadsheet URL."""
    import re
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    
    service = get_sheets_service()
    
    # 1. Sync balances sheet
    balances_title = build_balances_sheet_title()
    balances_values = build_balances_export(db, user_id)
    balances_gid = ensure_sheet(spreadsheet_id, balances_title)
    clear_and_update_values(spreadsheet_id, balances_title, balances_values)
    format_balances_sheet(service, spreadsheet_id, balances_gid)
    
    # 2. Get current months and create a set of expected sheet titles
    months = get_user_transaction_months(db, user_id)
    expected_month_titles = {build_month_sheet_title(year, month) for year, month in months}
    
    # 3. Delete old monthly sheets that are no longer needed
    all_sheets = get_all_sheet_titles(spreadsheet_id)
    month_pattern = re.compile(r'^\d{4}-\d{2}$')  # Matches YYYY-MM
    for sheet_title in all_sheets:
        # If it looks like a monthly sheet but isn't in our expected set, delete it
        if month_pattern.match(sheet_title) and sheet_title not in expected_month_titles:
            logger.info(f"Deleting old monthly sheet: {sheet_title}")
            delete_sheet_by_title(spreadsheet_id, sheet_title)
    
    # 4. Sync monthly sheets (current months)
    for year, month in months:
        month_title = build_month_sheet_title(year, month)
        month_values = build_month_transactions_export(db, user_id, year, month)
        month_gid = ensure_sheet(spreadsheet_id, month_title)
        clear_and_update_values(spreadsheet_id, month_title, month_values)
        format_month_sheet(service, spreadsheet_id, month_gid)
    
    logger.info(f"Google Sheets sync completed for user {user_id}")
    # Return URL pointing to balances sheet
    return get_spreadsheet_url(spreadsheet_id, gid=balances_gid)


async def sync_user_to_sheets_async(db: Session, user_id: int, spreadsheet_id: str) -> str:
    """Async wrapper for sync_user_to_sheets."""
    return await asyncio.to_thread(sync_user_to_sheets, db, user_id, spreadsheet_id)
