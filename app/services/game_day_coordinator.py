"""Game-day coordinator: tiered polling + event chains (Plan A2).

One APScheduler job ticks run_tick() every 5 minutes. Each tick is a
state-reconciliation pass -- everything derives from the DB vs the ESPN
scoreboard, never from "what time is it", so the first tick after any
downtime self-heals (sleeping-laptop reality).

Tiers per ET day: DORMANT (no games / day complete) -> PRE-GAME (before
first tip - 30 min) -> LIVE (scoreboard each tick) -> POST (all final,
chains done). The day verdict is memoized in-process; a restart simply
re-checks once.

Caching hard rule (spec): LIVE reads use nba_service.fetch_espn_scoreboard
directly (uncached) -- never a cache with TTL >= the tick interval.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from app import db
from app.enums import Outcome
from app.models import Bet, GameSnapshot, JobLog
from app.services.espn_history_append import (
    append_final_game, history_rows_exist,
)
from app.services.nba_service import _STATUS_FINAL, fetch_espn_scoreboard
from app.services.scheduler import resolve_and_grade
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 3
PREGAME_LEAD_MINUTES = 30

# {et_date: {'has_games': bool, 'first_tip': datetime|None, 'done': bool,
#            'seen_final': set[str]}}
_DAY_CACHE: dict = {}


def _first_tip(games) -> datetime | None:
    tips = []
    for g in games:
        try:
            tips.append(datetime.fromisoformat(
                g.get('start_time', '').replace('Z', '+00:00')
            ).astimezone(ET))
        except ValueError:
            continue
    return min(tips) if tips else None


def _game_et_date(game) -> date | None:
    """ET calendar date the game is played on, or None if unparseable.

    Used to guard against ESPN's dateless scoreboard endpoint, which during
    the off-season returns the LAST PLAYED league day (not an empty list) --
    without this filter, month-old games get treated as today's slate.
    """
    try:
        return datetime.fromisoformat(
            game.get('start_time', '').replace('Z', '+00:00')
        ).astimezone(ET).date()
    except ValueError:
        return None


def _catch_up_lookback(today) -> int:
    """Append history for regular-season final games on the previous
    LOOKBACK_DAYS dates.

    HistoricalGameLog is deliberately regular-season-only (matches the
    hoopR import + weekly reconcile job), so playoff/preseason/Summer
    League finals are never appended here.
    """
    appended = 0
    for delta in range(1, LOOKBACK_DAYS + 1):
        day = today - timedelta(days=delta)
        for game in fetch_espn_scoreboard(day.strftime('%Y%m%d')):
            if (game.get('status') == _STATUS_FINAL
                    and game.get('season_type') == 2):
                appended += 1 if append_final_game(game) else 0
    return appended


def _needs_resolve(final_games) -> bool:
    """True when the DB disagrees with a final scoreboard on bets/snapshots."""
    if Bet.query.filter_by(outcome=Outcome.PENDING.value).count():
        return True
    for g in final_games:
        snap = GameSnapshot.query.filter_by(
            espn_id=str(g.get('espn_id'))).first()
        if snap is not None and not snap.is_final:
            return True
    return False


def _unresolved_final_ids(final_games, seen_final: set) -> set:
    """Final-game ids not yet known-settled.

    "Known-settled" means either this process already chained the game
    earlier today (seen_final) or the DB already has a finalized snapshot
    for it (a prior, real resolve_and_grade run already handled it) --
    either way there's nothing new to resolve for that specific game.
    """
    ids = set()
    for g in final_games:
        espn_id = str(g.get('espn_id'))
        if espn_id in seen_final:
            continue
        snap = GameSnapshot.query.filter_by(espn_id=espn_id).first()
        if snap is not None and snap.is_final:
            continue
        ids.add(espn_id)
    return ids


def _run_chain(final_games, unresolved_ids: set) -> None:
    """Grade/postmortem/finalize (existing idempotent job) + history append."""
    chained_ids, steps = [], []
    if unresolved_ids or _needs_resolve(final_games):
        resolve_and_grade()
        steps.append('resolve_and_grade')
    for g in final_games:
        if g.get('season_type') != 2:
            continue    # regular-season only -- matches HistoricalGameLog
        espn_id = str(g.get('espn_id'))
        if not history_rows_exist(espn_id):
            inserted = append_final_game(g)
            if inserted:
                chained_ids.append(espn_id)
                steps.append(f'history+{inserted}')
    if steps:
        job = JobLog(job_name='game-final-chain',
                     started_at=datetime.now(timezone.utc),
                     finished_at=datetime.now(timezone.utc),
                     status='success',
                     message=f"games={','.join(chained_ids) or '-'} "
                             f"steps={';'.join(steps)}")
        db.session.add(job)
        db.session.commit()


def run_tick(now: datetime | None = None) -> str:
    """One coordinator pass; returns the tier it acted in."""
    now = now or datetime.now(ET)
    today = now.date()

    state = _DAY_CACHE.get(today)
    if state is None:
        games = fetch_espn_scoreboard()
        games = [g for g in games if _game_et_date(g) == today]
        state = {'has_games': bool(games),
                 'first_tip': _first_tip(games), 'done': False,
                 'seen_final': set()}
        _DAY_CACHE.clear()
        _DAY_CACHE[today] = state
        appended = _catch_up_lookback(today)
        if appended:
            logger.info("coordinator: lookback appended %d games", appended)

    if not state['has_games'] or state['done']:
        return 'dormant'

    if state['first_tip'] and now < state['first_tip'] - timedelta(
            minutes=PREGAME_LEAD_MINUTES):
        return 'pre-game'

    games = fetch_espn_scoreboard()          # fresh LIVE read (uncached)
    games = [g for g in games if _game_et_date(g) == today]
    final_games = [g for g in games if g.get('status') == _STATUS_FINAL]
    final_ids = {str(g.get('espn_id')) for g in final_games}
    unresolved_ids = _unresolved_final_ids(final_games, state['seen_final'])
    # Non-regular-season finals are never appended (see _run_chain), so they
    # must not count as "pending" -- otherwise a playoff-only day could
    # never reach 'post' (append never succeeds, so history never exists).
    pending_work = [
        g for g in final_games
        if g.get('season_type') == 2
        and not history_rows_exist(str(g.get('espn_id')))
    ]
    if pending_work or unresolved_ids or _needs_resolve(final_games):
        _run_chain(final_games, unresolved_ids)
        state['seen_final'] |= final_ids
        return 'live'

    if games and len(final_games) == len(games):
        state['done'] = True
        return 'post'
    return 'live'
