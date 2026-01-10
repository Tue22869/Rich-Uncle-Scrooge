"""Google Sheets formatting utilities."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def format_balances_sheet(service, spreadsheet_id: str, sheet_id: int) -> None:
    """Apply formatting to Balances sheet."""
    requests = [
        # Set column widths
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 1
                },
                "properties": {"pixelSize": 200},
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": 2
                },
                "properties": {"pixelSize": 80},
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 2,
                    "endIndex": 3
                },
                "properties": {"pixelSize": 120},
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 3,
                    "endIndex": 4
                },
                "properties": {"pixelSize": 100},
                "fields": "pixelSize"
            }
        },
        # Make header row bold
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 11},
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)"
            }
        },
        # Freeze header rows
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 3}
                },
                "fields": "gridProperties.frozenRowCount"
            }
        }
    ]
    
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()
    except Exception as e:
        logger.warning(f"Failed to format Balances sheet: {e}")


def format_month_sheet(service, spreadsheet_id: str, sheet_id: int) -> None:
    """Apply formatting to monthly transactions sheet."""
    requests = [
        # Set column widths
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 1
                },
                "properties": {"pixelSize": 100},  # Date
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": 2
                },
                "properties": {"pixelSize": 80},  # Type
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 2,
                    "endIndex": 3
                },
                "properties": {"pixelSize": 100},  # Amount
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 3,
                    "endIndex": 4
                },
                "properties": {"pixelSize": 70},  # Currency
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 4,
                    "endIndex": 5
                },
                "properties": {"pixelSize": 120},  # Account
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 5,
                    "endIndex": 6
                },
                "properties": {"pixelSize": 150},  # Category
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 6,
                    "endIndex": 7
                },
                "properties": {"pixelSize": 250},  # Description
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 8,
                    "endIndex": 9
                },
                "properties": {"pixelSize": 200},  # Summary label
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 9,
                    "endIndex": 10
                },
                "properties": {"pixelSize": 120},  # Summary value
                "fields": "pixelSize"
            }
        },
        # Make header row bold
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": 0,
                    "endColumnIndex": 10
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 11},
                        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)"
            }
        },
        # Format summary column header
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2,
                    "endRowIndex": 3,
                    "startColumnIndex": 8,
                    "endColumnIndex": 10
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 11},
                        "backgroundColor": {"red": 0.95, "green": 0.85, "blue": 0.7}
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)"
            }
        },
        # Freeze header rows
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 3}
                },
                "fields": "gridProperties.frozenRowCount"
            }
        }
    ]
    
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()
    except Exception as e:
        logger.warning(f"Failed to format month sheet: {e}")

