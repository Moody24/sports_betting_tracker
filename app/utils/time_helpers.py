"""Timezone-aware date/time helpers (America/New_York)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_ET = ET  # backward-compat alias


def et_now() -> datetime:
    """Current datetime in Eastern Time."""
    return datetime.now(_ET)


def et_today() -> date:
    """Current date in Eastern Time."""
    return datetime.now(_ET).date()


def et_date_str() -> str:
    """Current date in Eastern Time as 'YYYY-MM-DD'."""
    return datetime.now(_ET).strftime("%Y-%m-%d")
