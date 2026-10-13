"""Defensive matchup data service.

Fetches and caches team defensive profiles to support the projection
engine's matchup adjustment calculations.
"""

import logging
import time
from datetime import datetime, timezone, date as date_type

from requests import RequestException

from app.config_display import STAT_KEY_TO_OPP_ALLOWED, PROP_TO_OPP_ALLOWED
from app.utils.time_helpers import et_today, ET

from app import db
from app.models import TeamDefenseSnapshot
from app.services.data_receipts import record_fetch
from app.services.historical_defense import observed_team_defense
from app.services.provider_access import require_nba_stats_access

logger = logging.getLogger(__name__)
APP_TIMEZONE = ET

_NBA_API_DELAY = 0.6

# Process-level cache for team defense lookups.
# get_team_defense() fires an ilike DB query per call; with 100-300 props
# per scoring run and only ~15 unique opponents, caching eliminates ~280 redundant queries.
# On a cache miss we load ALL 30 teams at once (one query), then do Python-side
# substring matching — preserves original ilike semantics without separate DB queries.
_TEAM_DEFENSE_CACHE: dict[tuple, dict] = {}   # (name_lower, date_str) → result dict
_TEAM_DEFENSE_CACHE_TS: dict[tuple, float] = {}
_TEAM_DEFENSE_ALL_ROWS: dict[str, list] = {}   # date_str → list of all result dicts
_TEAM_DEFENSE_ALL_LOADED: dict[str, float] = {}  # date_str → monotonic ts of bulk-load
_TEAM_DEFENSE_TTL = 3600  # 1 h; defense data changes only once per scheduler run

# League-average baselines (approximate, updated each season)
LEAGUE_AVG = {
    'pts': 114.0,
    'reb': 44.0,
    'ast': 25.5,
    'fg3m': 12.5,
    'stl': 7.5,
    'blk': 5.0,
    'tov': 14.0,
    'pace': 100.0,
}

POSITION_POINT_BASELINES = {
    'pg': 27.0,
    'sg': 25.0,
    'sf': 23.0,
    'pf': 21.0,
    'c': 18.0,
}

def fetch_team_defense_stats() -> list:
    """Fetch opponent (defensive) stats for all NBA teams from the NBA API.

    Returns a list of dicts with defensive metrics per team.
    """
    try:
        require_nba_stats_access()
    except RequestException as exc:
        logger.warning('%s', exc)
        return []

    try:
        from nba_api.stats.endpoints import leaguedashteamstats
    except ImportError:
        logger.error("nba_api package not installed")
        return []

    try:
        stats = leaguedashteamstats.LeagueDashTeamStats(
            measure_type_detailed_defense='Opponent',
            per_mode_detailed='PerGame',
            season_type_all_star='Regular Season',
        )
        time.sleep(_NBA_API_DELAY)
        df = stats.get_data_frames()[0]
    except Exception as exc:
        logger.error("NBA API team defense fetch failed: %s", exc)
        return []

    if df.empty:
        return []

    results = []
    for _, row in df.iterrows():
        pace_val = float(row.get('PACE', 0) or row.get('OPP_PACE', 0) or 0)
        def_rating_val = float(row.get('DEF_RATING', 0) or 0)

        results.append({
            'team_id': str(row.get('TEAM_ID', '')),
            'team_name': str(row.get('TEAM_NAME', '')),
            'team_abbr': str(row.get('TEAM_ABBREVIATION', '')),
            'opp_pts_pg': float(row.get('OPP_PTS', 0) or 0),
            'opp_reb_pg': float(row.get('OPP_REB', 0) or 0),
            'opp_ast_pg': float(row.get('OPP_AST', 0) or 0),
            'opp_3pm_pg': float(row.get('OPP_FG3M', 0) or 0),
            'opp_stl_pg': float(row.get('OPP_STL', 0) or 0),
            'opp_blk_pg': float(row.get('OPP_BLK', 0) or 0),
            'opp_tov_pg': float(row.get('OPP_TOV', 0) or 0),
            'pace': pace_val or None,
            'def_rating': def_rating_val or None,
            'sample_games': int(row.get('GP', 0) or 0),
            'opp_pts_allowed_pg': row.get('OPP_PTS_ALLOWED_PG'),
            'opp_pts_allowed_sg': row.get('OPP_PTS_ALLOWED_SG'),
            'opp_pts_allowed_sf': row.get('OPP_PTS_ALLOWED_SF'),
            'opp_pts_allowed_pf': row.get('OPP_PTS_ALLOWED_PF'),
            'opp_pts_allowed_c': row.get('OPP_PTS_ALLOWED_C'),
        })

    return results



def refresh_all_team_defense() -> int:
    """Refresh team defense from completed games or the NBA endpoint."""
    today = et_today()
    team_stats = observed_team_defense(today) or fetch_team_defense_stats()
    if not team_stats:
        record_fetch('nba_team_defense', 'failed', detail='No observed team data returned')
        db.session.commit()
        logger.warning("No observed team defense data fetched")
        return 0
    return save_team_defense(team_stats, today)


_DEFENSE_FIELDS = (
    'team_name', 'team_abbr', 'source', 'observed_through', 'sample_games',
    'opp_pts_pg', 'opp_reb_pg', 'opp_ast_pg', 'opp_3pm_pg', 'opp_stl_pg',
    'opp_blk_pg', 'opp_tov_pg', 'pace', 'def_rating',
    'opp_pts_allowed_pg', 'opp_pts_allowed_sg', 'opp_pts_allowed_sf',
    'opp_pts_allowed_pf', 'opp_pts_allowed_c',
)


def save_team_defense(team_stats: list[dict], snapshot_date: date_type,
                      *, source_version: str | None = None) -> int:
    """Upsert observed team profiles and retain their actual as-of dates."""
    if not team_stats:
        return 0

    count = 0
    retained_release = False
    observed_dates = []
    for ts in team_stats:
        existing = TeamDefenseSnapshot.query.filter_by(
            team_id=ts['team_id'],
            snapshot_date=snapshot_date,
        ).first()
        if (existing is not None
                and (existing.source or '').startswith('sportsdataverse_team_')
                and (ts.get('source') or '').startswith('espn_boxscore_')
                and existing.observed_through
                and existing.observed_through >= (ts.get('observed_through') or date_type.min)):
            retained_release = True
            observed_dates.append(existing.observed_through)
            count += 1
            continue
        if existing is None:
            existing = TeamDefenseSnapshot(
                team_id=ts['team_id'], snapshot_date=snapshot_date,
            )
            db.session.add(existing)
        for field in _DEFENSE_FIELDS:
            setattr(existing, field, ts.get(field))
        existing.source = ts.get('source', 'nba_api')
        existing.fetched_at = datetime.now(timezone.utc)
        if existing.observed_through:
            observed_dates.append(existing.observed_through)
        count += 1

    record_fetch('nba_team_defense', 'success', count,
                 data_through=max(observed_dates) if observed_dates else None,
                 source_version=(source_version or
                                 ('retained_sportsdataverse_release' if retained_release
                                  else team_stats[0].get('source', 'nba_api'))))
    db.session.commit()
    invalidate_team_defense_cache()
    logger.info("Refreshed defense data for %d teams", count)
    return count


def invalidate_team_defense_cache() -> None:
    """Clear the process-level team defense cache.

    Call after ``refresh_team_defense()`` so the next lookup picks up fresh data.
    """
    _TEAM_DEFENSE_CACHE.clear()
    _TEAM_DEFENSE_CACHE_TS.clear()
    _TEAM_DEFENSE_ALL_ROWS.clear()
    _TEAM_DEFENSE_ALL_LOADED.clear()
    logger.debug("team_defense_cache: invalidated")


def get_team_defense(team_name: str, date: date_type = None) -> dict:
    """Look up the most recent defensive snapshot for a team.

    Matches by substring on ``team_name`` (case-insensitive).
    Returns a dict of defensive stats or empty dict if not found.

    Results are cached process-wide for 1 h to avoid an ilike DB query
    on every call (the function is invoked once per player-prop via
    ``get_matchup_adjustment``).
    """
    if date is None:
        date = et_today()

    date_str = str(date)
    cache_key = (team_name.lower().strip(), date_str)
    now = time.monotonic()
    if cache_key in _TEAM_DEFENSE_CACHE and now - _TEAM_DEFENSE_CACHE_TS.get(cache_key, 0) < _TEAM_DEFENSE_TTL:
        return _TEAM_DEFENSE_CACHE[cache_key]

    # Bulk-load all teams for this date in one query (max 30 rows) so that
    # subsequent per-team calls use Python-side matching instead of separate DB queries.
    # Skip TTL in test mode: each test creates a fresh in-memory DB and the
    # module-level cache would return stale rows from the previous test's DB.
    from flask import current_app
    testing = current_app.config.get('TESTING', False)
    all_loaded_ts = _TEAM_DEFENSE_ALL_LOADED.get(date_str, 0)
    if testing or now - all_loaded_ts >= _TEAM_DEFENSE_TTL:
        snaps = (
            TeamDefenseSnapshot.query
            .filter(TeamDefenseSnapshot.snapshot_date <= date)
            .order_by(TeamDefenseSnapshot.snapshot_date.desc())
            .all()
        )
        seen_teams: set[str] = set()
        rows_for_date: list = []
        for snap in snaps:
            # keep only the latest snapshot per team
            tid = snap.team_id or snap.team_name
            if tid in seen_teams:
                continue
            seen_teams.add(tid)
            entry = {
                'team_id': snap.team_id,
                'team_name': snap.team_name,
                'team_abbr': snap.team_abbr,
                'source': snap.source,
                'observed_through': snap.observed_through,
                'sample_games': snap.sample_games,
                'opp_pts_pg': snap.opp_pts_pg or 0,
                'opp_reb_pg': snap.opp_reb_pg or 0,
                'opp_ast_pg': snap.opp_ast_pg or 0,
                'opp_3pm_pg': snap.opp_3pm_pg or 0,
                'opp_stl_pg': snap.opp_stl_pg or 0,
                'opp_blk_pg': snap.opp_blk_pg or 0,
                'opp_tov_pg': snap.opp_tov_pg or 0,
                'pace': snap.pace or 0,
                'def_rating': snap.def_rating or 0,
                'opp_pts_allowed_pg': snap.opp_pts_allowed_pg or 0,
                'opp_pts_allowed_sg': snap.opp_pts_allowed_sg or 0,
                'opp_pts_allowed_sf': snap.opp_pts_allowed_sf or 0,
                'opp_pts_allowed_pf': snap.opp_pts_allowed_pf or 0,
                'opp_pts_allowed_c': snap.opp_pts_allowed_c or 0,
            }
            rows_for_date.append(entry)
        _TEAM_DEFENSE_ALL_ROWS[date_str] = rows_for_date
        _TEAM_DEFENSE_ALL_LOADED[date_str] = now

    # Python-side substring match — replicates original ilike('%term%') semantics
    # but operates on the in-memory list instead of hitting the DB.
    term = team_name.lower().strip()
    result: dict = {}
    for entry in _TEAM_DEFENSE_ALL_ROWS.get(date_str, []):
        if (term in (entry.get('team_name') or '').lower()
                or term in (entry.get('team_abbr') or '').lower()):
            result = entry
            break
    _TEAM_DEFENSE_CACHE[cache_key] = result
    _TEAM_DEFENSE_CACHE_TS[cache_key] = now
    return result


def get_position_matchup_adjustment(opponent_name: str, position: str) -> float:
    """Return a points adjustment factor based on opponent vs player position."""
    position_key = (position or '').strip().lower()
    field_map = {
        'pg': 'opp_pts_allowed_pg',
        'sg': 'opp_pts_allowed_sg',
        'sf': 'opp_pts_allowed_sf',
        'pf': 'opp_pts_allowed_pf',
        'c': 'opp_pts_allowed_c',
    }
    field = field_map.get(position_key)
    if not field:
        return 1.0

    defense = get_team_defense(opponent_name)
    if not defense:
        return 1.0

    allowed = float(defense.get(field, 0) or 0)
    league_baseline = float(POSITION_POINT_BASELINES.get(position_key, 0) or 0)
    if allowed <= 0 or league_baseline <= 0:
        return 1.0
    return round(allowed / league_baseline, 3)


def get_matchup_adjustment(opponent_name: str, stat_type: str) -> float:
    """Calculate a matchup adjustment multiplier for a stat type.

    Returns a factor > 1.0 if the opponent allows more than league average,
    < 1.0 if they allow less.  Returns 1.0 on missing data.
    """
    defense = get_team_defense(opponent_name)
    if not defense:
        return 1.0

    # Accepts both stat keys (pts, reb) and prop types (player_points, etc.)
    defense_key = STAT_KEY_TO_OPP_ALLOWED.get(stat_type) or PROP_TO_OPP_ALLOWED.get(stat_type)
    if not defense_key:
        return 1.0

    opp_allowed = defense.get(defense_key, 0)
    # Map to the league average key
    avg_key = defense_key.replace('opp_', '').replace('_pg', '')
    league_avg = LEAGUE_AVG.get(avg_key, 0)

    if league_avg <= 0:
        return 1.0

    return round(opp_allowed / league_avg, 3)


def get_pace_factor(opponent_name: str) -> float:
    """Return a pace multiplier relative to league average.

    > 1.0 = faster than average, < 1.0 = slower.
    """
    defense = get_team_defense(opponent_name)
    if not defense or not defense.get('pace'):
        return 1.0

    league_pace = LEAGUE_AVG.get('pace', 100.0)
    if league_pace <= 0:
        return 1.0

    return round(defense['pace'] / league_pace, 3)
