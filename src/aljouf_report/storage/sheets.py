"""Mirror the SQLite tables into a Google Sheet.

Uses a service account, so no interactive Google login is needed. Share the
target spreadsheet with the service account email as an Editor once, and the
pipeline can write to it forever after.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from ..config import AppConfig

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# worksheet title -> source table/view
TABS: dict[str, str] = {
    "Followers": "follower_snapshots",
    "News": "news_articles",
    "Comments": "v_comments_scored",
    "Runs": "runs",
}

# Columns dropped from the Sheet: huge JSON blobs make the sheet unusable and
# hit the 50k-character-per-cell limit.
SKIP_COLUMNS = {"raw_json"}

# Google Sheets caps a single cell at 50,000 characters.
MAX_CELL_CHARS = 49_000


class SheetsSyncError(RuntimeError):
    pass


def _stringify(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    # A leading '=' or '+' would be parsed as a formula; comments can contain both.
    if text[:1] in {"=", "+", "-", "@"} and not isinstance(value, (int, float)):
        text = "'" + text
    return text[:MAX_CELL_CHARS]


def _to_grid(rows: Sequence[dict[str, Any]]) -> list[list[Any]]:
    if not rows:
        return [["(no data)"]]
    headers = [c for c in rows[0].keys() if c not in SKIP_COLUMNS]
    grid: list[list[Any]] = [headers]
    for row in rows:
        grid.append([_stringify(row.get(h)) for h in headers])
    return grid


class SheetsSync:
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.settings.storage.sheets_enabled and self.config.secrets.sheets_ready

    def push(self, tables: dict[str, Sequence[dict[str, Any]]]) -> list[str]:
        """Replace each worksheet's contents. Returns the tabs written."""
        if not self.enabled:
            log.info(
                "Google Sheets sync skipped (set GOOGLE_SHEETS_ID and a service "
                "account to enable it)."
            )
            return []

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SheetsSyncError(
                "gspread/google-auth are not installed. Run: pip install -r requirements.txt"
            ) from exc

        creds_info = self.config.secrets.google_credentials
        if not creds_info:
            raise SheetsSyncError("No Google service account credentials resolved.")

        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        client = gspread.authorize(creds)

        try:
            spreadsheet = client.open_by_key(self.config.secrets.google_sheets_id)
        except Exception as exc:
            raise SheetsSyncError(
                f"Could not open spreadsheet {self.config.secrets.google_sheets_id!r}. "
                f"Confirm the ID and that it is shared with "
                f"{creds_info.get('client_email')} as Editor. ({exc})"
            ) from exc

        written: list[str] = []
        for tab, rows in tables.items():
            grid = _to_grid(list(rows))
            try:
                worksheet = spreadsheet.worksheet(tab)
                worksheet.clear()
            except Exception:
                worksheet = spreadsheet.add_worksheet(
                    title=tab, rows=max(len(grid) + 10, 100), cols=max(len(grid[0]) + 2, 20)
                )
            worksheet.update(values=grid, range_name="A1")
            worksheet.freeze(rows=1)
            written.append(tab)
            log.info("Sheets: wrote %d rows to tab '%s'", len(grid) - 1, tab)
        return written


def sync_all(config: AppConfig, db) -> list[str]:
    """Push every mirrored table. Errors are logged, never fatal."""
    syncer = SheetsSync(config)
    if not syncer.enabled:
        return []
    payload = {tab: db.table_rows(table) for tab, table in TABS.items()}
    try:
        return syncer.push(payload)
    except SheetsSyncError as exc:
        log.warning("Google Sheets sync failed: %s", exc)
        return []
