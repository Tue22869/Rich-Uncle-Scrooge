"""Google Sheets API client helpers.

Supports two auth modes:
- Service account via GOOGLE_APPLICATION_CREDENTIALS (recommended for bots)
- OAuth refresh token via GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN
"""

from __future__ import annotations

import os
import logging
from typing import Optional

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleSheetsNotConfigured(RuntimeError):
    pass


def _get_credentials():
    # Service account (preferred)
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if sa_path:
        return ServiceAccountCredentials.from_service_account_file(sa_path, scopes=SCOPES)

    # OAuth refresh token (no interactive flow in bot runtime)
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()

    if client_id and client_secret and refresh_token:
        return UserCredentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

    raise GoogleSheetsNotConfigured(
        "Google Sheets auth is not configured. Set GOOGLE_APPLICATION_CREDENTIALS (service account) "
        "or GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET/GOOGLE_OAUTH_REFRESH_TOKEN."
    )


def get_service_account_email() -> Optional[str]:
    """Best-effort: return service account email if GOOGLE_APPLICATION_CREDENTIALS is set."""
    sa_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not sa_path:
        return None
    try:
        import json

        with open(sa_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("client_email")
    except Exception:
        return None


def is_configured() -> bool:
    try:
        _get_credentials()
        return True
    except Exception:
        return False


def get_sheets_service():
    creds = _get_credentials()
    # cache_discovery=False avoids creating cache files
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_spreadsheet_url(spreadsheet_id: Optional[str] = None, gid: Optional[int] = None) -> str:
    if not spreadsheet_id:
        raise ValueError("spreadsheet_id is required")
    sid = spreadsheet_id
    if gid is None:
        return f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid={gid}"


def ensure_sheet(spreadsheet_id: str, title: str) -> int:
    """Ensure a sheet(tab) exists and return its sheetId (gid)."""
    service = get_sheets_service()
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))")
        .execute()
    )
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == title:
            return int(props["sheetId"])

    body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    resp = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    replies = resp.get("replies", [])
    if not replies or "addSheet" not in replies[0]:
        raise RuntimeError("Failed to create sheet")
    return int(replies[0]["addSheet"]["properties"]["sheetId"])


def clear_and_update_values(spreadsheet_id: str, sheet_title: str, values: list[list[object]]) -> None:
    """Replace the entire sheet starting from A1 with provided values."""
    service = get_sheets_service()
    # Clear the entire sheet (not just A1!)
    clear_range = f"'{sheet_title}'"
    service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=clear_range, body={}).execute()
    
    # Write new values starting from A1
    update_range = f"'{sheet_title}'!A1"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=update_range,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def read_sheet_values(spreadsheet_id: str, sheet_title: str) -> list[list]:
    """Read all values from a sheet. Returns 2D list of cell values."""
    service = get_sheets_service()
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'")
            .execute()
        )
        return result.get("values", [])
    except Exception as e:
        logger.error(f"Failed to read sheet {sheet_title}: {e}")
        raise


def get_all_sheet_titles(spreadsheet_id: str) -> list[str]:
    """Get all sheet titles in a spreadsheet."""
    service = get_sheets_service()
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(title))")
        .execute()
    )
    return [sh["properties"]["title"] for sh in meta.get("sheets", [])]


def delete_sheet_by_title(spreadsheet_id: str, title: str) -> None:
    """Delete a sheet by title. Does nothing if sheet doesn't exist."""
    service = get_sheets_service()
    # Get sheet ID
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))")
        .execute()
    )
    sheet_id = None
    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("title") == title:
            sheet_id = props["sheetId"]
            break
    
    if sheet_id is None:
        return  # Sheet doesn't exist
    
    body = {"requests": [{"deleteSheet": {"sheetId": sheet_id}}]}
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    logger.info(f"Deleted sheet: {title}")


