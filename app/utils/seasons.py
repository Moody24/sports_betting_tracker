"""Season-label helpers shared by commands and background services."""

from datetime import date, datetime

from app.utils.time_helpers import ET


def recent_nba_seasons(n: int, today: date | None = None) -> list[str]:
    """Return the most recent NBA season strings, newest first."""
    today = today or datetime.now(ET).date()
    start_year = today.year if today.month >= 10 else today.year - 1
    return [
        f"{year}-{str(year + 1)[-2:]}"
        for year in range(start_year, start_year - n, -1)
    ]
