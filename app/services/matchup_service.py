"""Defensive matchup data service.

Fetches and caches team defensive profiles to support the projection
engine's matchup adjustment calculations.
"""

import logging
import time
from datetime import datetime, timezone, date as date_type

from app import db
from app.models import TeamDefenseSnapshot

logger = logging.getLogger(__name__)

_NBA_API_DELAY = 0.6

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


def _build_baseline_team_stats() -> list:
    """Fallback baseline when live NBA defensive endpoint is unavailable."""
    try:
        from nba_api.stats.static import teams as nba_static_teams
        teams = nba_static_teams.get_teams()
    except Exception:
        teams = []

    baseline = []
    for team in teams:
        baseline.append({
            'team_id': str(team.get('id', '')),
            'team_name': str(team.get('full_name', '')),
            'team_abbr': str(team.get('abbreviation', '')),
            'opp_pts_pg': LEAGUE_AVG['pts'],
            'opp_reb_pg': LEAGUE_AVG['reb'],
            'opp_ast_pg': LEAGUE_AVG['ast'],
            'opp_3pm_pg': LEAGUE_AVG['fg3m'],
            'opp_stl_pg': LEAGUE_AVG['stl'],
            'opp_blk_pg': LEAGUE_AVG['blk'],
            'opp_tov_pg': LEAGUE_AVG['tov'],
            'pace': LEAGUE_AVG['pace'],
            'def_rating': 114.0,
        })
    return baseline


def fetch_team_defense_stats() -> list:
    """Fetch opponent (defensive) stats for all NBA teams from the NBA API.

    Returns a list of dicts with defensive metrics per team.
    """
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
            'pace': float(row.get('PACE', 0) or row.get('OPP_PACE', 0) or 0),
            'def_rating': float(row.get('DEF_RATING', 0) or 0),
        })

    return results


def refresh_all_team_defense() -> int:
    """Refresh defensive snapshots for all 30 NBA teams.

    Fetches from NBA API and upserts into ``TeamDefenseSnapshot``.
    Returns the number of teams updated.
    """
    today = date_type.today()
    team_stats = fetch_team_defense_stats()
    if not team_stats:
        team_stats = _build_baseline_team_stats()
        if not team_stats:
            logger.warning("No team defense data fetched")
            return 0
        logger.warning("Using baseline defensive snapshot fallback (%d teams)", len(team_stats))

    count = 0
    for ts in team_stats:
        existing = TeamDefenseSnapshot.query.filter_by(
            team_id=ts['team_id'],
            snapshot_date=today,
        ).first()

        if existing:
            for key, val in ts.items():
                if key not in ('team_id',) and hasattr(existing, key):
                    setattr(existing, key, val)
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            snap = TeamDefenseSnapshot(
                team_id=ts['team_id'],
                team_name=ts['team_name'],
                team_abbr=ts.get('team_abbr', ''),
                snapshot_date=today,
                opp_pts_pg=ts.get('opp_pts_pg', 0),
                opp_reb_pg=ts.get('opp_reb_pg', 0),
                opp_ast_pg=ts.get('opp_ast_pg', 0),
                opp_3pm_pg=ts.get('opp_3pm_pg', 0),
                opp_stl_pg=ts.get('opp_stl_pg', 0),
                opp_blk_pg=ts.get('opp_blk_pg', 0),
                opp_tov_pg=ts.get('opp_tov_pg', 0),
                pace=ts.get('pace', 0),
                def_rating=ts.get('def_rating', 0),
            )
            db.session.add(snap)
        count += 1

    db.session.commit()
    logger.info("Refreshed defense data for %d teams", count)
    return count


def get_team_defense(team_name: str, date: date_type = None) -> dict:
    """Look up the most recent defensive snapshot for a team.

    Matches by substring on ``team_name`` (case-insensitive).
    Returns a dict of defensive stats or empty dict if not found.
    """
    if date is None:
        date = date_type.today()

    snap = (
        TeamDefenseSnapshot.query
        .filter(TeamDefenseSnapshot.team_name.ilike(f'%{team_name}%'))
        .filter(TeamDefenseSnapshot.snapshot_date <= date)
        .order_by(TeamDefenseSnapshot.snapshot_date.desc())
        .first()
    )

    if not snap:
        return {}

    return {
        'team_id': snap.team_id,
        'team_name': snap.team_name,
        'team_abbr': snap.team_abbr,
        'opp_pts_pg': snap.opp_pts_pg or 0,
        'opp_reb_pg': snap.opp_reb_pg or 0,
        'opp_ast_pg': snap.opp_ast_pg or 0,
        'opp_3pm_pg': snap.opp_3pm_pg or 0,
        'opp_stl_pg': snap.opp_stl_pg or 0,
        'opp_blk_pg': snap.opp_blk_pg or 0,
        'opp_tov_pg': snap.opp_tov_pg or 0,
        'pace': snap.pace or 0,
        'def_rating': snap.def_rating or 0,
    }


def get_matchup_adjustment(opponent_name: str, stat_type: str) -> float:
    """Calculate a matchup adjustment multiplier for a stat type.

    Returns a factor > 1.0 if the opponent allows more than league average,
    < 1.0 if they allow less.  Returns 1.0 on missing data.
    """
    defense = get_team_defense(opponent_name)
    if not defense:
        return 1.0

    stat_map = {
        'pts': 'opp_pts_pg',
        'player_points': 'opp_pts_pg',
        'reb': 'opp_reb_pg',
        'player_rebounds': 'opp_reb_pg',
        'ast': 'opp_ast_pg',
        'player_assists': 'opp_ast_pg',
        'fg3m': 'opp_3pm_pg',
        'player_threes': 'opp_3pm_pg',
        'stl': 'opp_stl_pg',
        'blk': 'opp_blk_pg',
    }

    defense_key = stat_map.get(stat_type)
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
