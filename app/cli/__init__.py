"""Flask CLI package — shared helpers, constants, and registration entry point."""

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app import db
from app.models import Bet, PickContext

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo("America/New_York")

BACKFILL_COMMIT_BATCH = 300
MAX_FETCH_FAILURES = 3


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize DB datetimes to UTC-aware for safe arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_player_ids(raw_player_ids: str) -> list[str]:
    if not raw_player_ids:
        return []
    return [pid.strip() for pid in raw_player_ids.split(',') if pid.strip()]


def _season_start_year(season: str) -> int:
    return int(str(season).split('-')[0])


def _resolved_win_rate(days: int):
    """Return segmented win rates for resolved bets with pick context in the last N days.

    Returns a dict with keys: manual, auto, real (manual+auto), bootstrap, all.
    Each value is (count, wins, rate) or None when that segment has no rows.
    Returns None when there are no matching rows at all.

    Segments:
      manual    — source='manual' bets placed by a real user
      auto      — source='auto_generated' real system picks (not bootstrap synthetic data)
      real      — manual + auto combined; used for drift comparison vs val_accuracy
      bootstrap — source='auto_generated' + notes starting with 'AUTO_BOOTSTRAP_HIDDEN';
                  synthetic training data — excluded from drift comparison to avoid
                  comparing the model against its own training set
      all       — every resolved bet with a PickContext
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(Bet, PickContext)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
        .filter(Bet.match_date >= cutoff)
        .all()
    )
    if not rows:
        return None

    def _rate(subset):
        if not subset:
            return None
        wins = sum(1 for b, _ in subset if b.outcome == 'win')
        return len(subset), wins, wins / len(subset)

    manual = [(b, pc) for b, pc in rows if b.source == 'manual']
    auto = [
        (b, pc) for b, pc in rows
        if b.source == 'auto_generated'
        and not (b.notes or '').startswith('AUTO_BOOTSTRAP_HIDDEN')
        and not (b.notes or '').startswith('AUTO_PAPER_COHORT:')
    ]
    bootstrap = [
        (b, pc) for b, pc in rows
        if b.source == 'auto_generated'
        and (b.notes or '').startswith('AUTO_BOOTSTRAP_HIDDEN')
    ]
    return {
        'manual': _rate(manual),
        'auto': _rate(auto),
        'real': _rate(manual + auto),
        'bootstrap': _rate(bootstrap),
        'all': _rate(rows),
    }


def register_cli(app):
    """Register all CLI commands with the Flask app."""
    from app.cli.stats_commands import register_stats_commands
    from app.cli.model_commands import register_model_commands
    from app.cli.market_commands import register_market_commands
    from app.cli.reporting_commands import register_reporting_commands
    register_stats_commands(app)
    register_model_commands(app)
    register_market_commands(app)
    register_reporting_commands(app)
