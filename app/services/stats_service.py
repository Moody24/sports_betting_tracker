"""Player stats ingestion service using NBA API.

Fetches and caches player game logs for players on tonight's slate.
The NBA API is the primary data warehouse; the database is a thin cache
that holds only active-slate player data.
"""

import logging
import time
from datetime import datetime, timezone, timedelta, date as date_type
from difflib import SequenceMatcher
from typing import Optional

from app import db
from app.models import PlayerGameLog

logger = logging.getLogger(__name__)

# Rate-limit delay between NBA API calls (seconds)
_NBA_API_DELAY = 0.6


class PlayerNameResolver:
    """Fuzzy-matches player names across data sources.

    The Odds API uses names like "LeBron James" while NBA API may have
    slight variations.  This resolver finds the best match using
    SequenceMatcher and caches resolved mappings.
    """

    def __init__(self):
        self._cache = {}

    def best_match(
        self, target: str, candidates: list, threshold: float = 0.75
    ) -> Optional[str]:
        """Return the best matching name from *candidates* for *target*.

        Returns ``None`` if no candidate exceeds the similarity *threshold*.
        """
        if not target or not candidates:
            return None

        cache_key = (target.lower().strip(), tuple(c.lower().strip() for c in candidates))
        if cache_key in self._cache:
            return self._cache[cache_key]

        target_lower = target.lower().strip()

        # Exact match first
        for c in candidates:
            if c.lower().strip() == target_lower:
                self._cache[cache_key] = c
                return c

        # Substring containment
        for c in candidates:
            c_lower = c.lower().strip()
            if target_lower in c_lower or c_lower in target_lower:
                self._cache[cache_key] = c
                return c

        # Fuzzy match
        best = None
        best_ratio = 0.0
        for c in candidates:
            ratio = SequenceMatcher(None, target_lower, c.lower().strip()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = c

        result = best if best_ratio >= threshold else None
        self._cache[cache_key] = result
        return result

    def clear_cache(self):
        self._cache.clear()


# Module-level resolver instance
name_resolver = PlayerNameResolver()


def _parse_minutes(min_str) -> float:
    """Parse minutes string (e.g. '34:12' or '34.5') into float minutes."""
    if min_str is None:
        return 0.0
    s = str(min_str).strip()
    if ':' in s:
        parts = s.split(':')
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def fetch_player_game_logs(player_id: str, season: str = None, last_n: int = 15):
    """Fetch recent game logs for a player from the NBA API.

    Returns a list of dicts with normalised stat keys, or an empty list
    on failure.
    """
    try:
        from nba_api.stats.endpoints import playergamelog
    except ImportError:
        logger.error("nba_api package not installed")
        return []

    if season is None:
        now = datetime.now(timezone.utc)
        year = now.year if now.month >= 10 else now.year - 1
        season = f"{year}-{str(year + 1)[-2:]}"

    try:
        log = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star='Regular Season',
        )
        time.sleep(_NBA_API_DELAY)
        df = log.get_data_frames()[0]
    except Exception as exc:
        logger.error("NBA API game log fetch failed for player %s: %s", player_id, exc)
        return []

    if df.empty:
        return []

    rows = []
    for _, row in df.head(last_n).iterrows():
        matchup = str(row.get('MATCHUP', ''))
        home_away = 'home' if 'vs.' in matchup else 'away'
        rows.append({
            'player_id': str(player_id),
            'player_name': str(row.get('PLAYER_NAME', '')),
            'team_abbr': str(row.get('TEAM_ABBREVIATION', '')),
            'game_date': _parse_game_date(row.get('GAME_DATE', '')),
            'matchup': matchup,
            'minutes': _parse_minutes(row.get('MIN', 0)),
            'pts': float(row.get('PTS', 0) or 0),
            'reb': float(row.get('REB', 0) or 0),
            'ast': float(row.get('AST', 0) or 0),
            'stl': float(row.get('STL', 0) or 0),
            'blk': float(row.get('BLK', 0) or 0),
            'tov': float(row.get('TOV', 0) or 0),
            'fgm': float(row.get('FGM', 0) or 0),
            'fga': float(row.get('FGA', 0) or 0),
            'ftm': float(row.get('FTM', 0) or 0),
            'fta': float(row.get('FTA', 0) or 0),
            'fg3m': float(row.get('FG3M', 0) or 0),
            'fg3a': float(row.get('FG3A', 0) or 0),
            'plus_minus': float(row.get('PLUS_MINUS', 0) or 0),
            'home_away': home_away,
            'win_loss': str(row.get('WL', ''))[:1],
        })

    return rows


def _parse_game_date(date_val) -> date_type:
    """Parse a game date from NBA API (various formats)."""
    if isinstance(date_val, date_type):
        return date_val
    if isinstance(date_val, datetime):
        return date_val.date()
    s = str(date_val).strip()
    for fmt in ('%b %d, %Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date_type.today()


def find_player_id(player_name: str) -> Optional[str]:
    """Look up an NBA API player ID by name.

    Uses nba_api's static player list for fast lookup with fuzzy matching.
    """
    try:
        from nba_api.stats.static import players as nba_players
    except ImportError:
        logger.error("nba_api package not installed")
        return None

    all_players = nba_players.get_active_players()
    # Try exact match first
    for p in all_players:
        if p['full_name'].lower() == player_name.lower().strip():
            return str(p['id'])

    # Fuzzy match
    names = [p['full_name'] for p in all_players]
    match = name_resolver.best_match(player_name, names, threshold=0.80)
    if match:
        for p in all_players:
            if p['full_name'] == match:
                return str(p['id'])

    return None


def cache_player_logs(player_id: str, game_logs: list, ttl_days: int = 7):
    """Upsert game logs into the database cache.

    Existing rows for the same player+date are updated; new rows are inserted.
    Sets cache_expires to ``ttl_days`` from now.
    """
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    for log in game_logs:
        existing = PlayerGameLog.query.filter_by(
            player_id=str(player_id),
            game_date=log['game_date'],
        ).first()

        if existing:
            for key, val in log.items():
                if key not in ('player_id', 'game_date') and hasattr(existing, key):
                    setattr(existing, key, val)
            existing.cache_expires = expires
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            row = PlayerGameLog(
                player_id=str(player_id),
                player_name=log['player_name'],
                team_abbr=log.get('team_abbr', ''),
                game_date=log['game_date'],
                matchup=log.get('matchup', ''),
                minutes=log.get('minutes', 0),
                pts=log.get('pts', 0),
                reb=log.get('reb', 0),
                ast=log.get('ast', 0),
                stl=log.get('stl', 0),
                blk=log.get('blk', 0),
                tov=log.get('tov', 0),
                fgm=log.get('fgm', 0),
                fga=log.get('fga', 0),
                ftm=log.get('ftm', 0),
                fta=log.get('fta', 0),
                fg3m=log.get('fg3m', 0),
                fg3a=log.get('fg3a', 0),
                plus_minus=log.get('plus_minus', 0),
                home_away=log.get('home_away', ''),
                win_loss=log.get('win_loss', ''),
                context_flags=log.get('context_flags'),
                cache_expires=expires,
            )
            db.session.add(row)

    db.session.commit()


def get_cached_logs(player_id: str, last_n: int = 15) -> list:
    """Retrieve cached game logs for a player, ordered by date descending."""
    rows = (
        PlayerGameLog.query
        .filter_by(player_id=str(player_id))
        .order_by(PlayerGameLog.game_date.desc())
        .limit(last_n)
        .all()
    )
    return rows


def get_player_stats_summary(player_id: str, logs: list = None) -> dict:
    """Compute stat averages over different windows from cached logs.

    Returns a dict with keys: last_5, last_10, season, std_dev, games_played.
    """
    if logs is None:
        logs = get_cached_logs(player_id, last_n=82)

    if not logs:
        return {
            'last_5': {}, 'last_10': {}, 'season': {},
            'std_dev': {}, 'games_played': 0,
        }

    stat_keys = ['pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'fg3m', 'minutes']

    def _averages(log_slice):
        if not log_slice:
            return {}
        result = {}
        for key in stat_keys:
            vals = [getattr(l, key, 0) or 0 for l in log_slice]
            result[key] = round(sum(vals) / len(vals), 1) if vals else 0
        return result

    def _std_devs(log_slice):
        if len(log_slice) < 2:
            return {}
        result = {}
        for key in stat_keys:
            vals = [getattr(l, key, 0) or 0 for l in log_slice]
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            result[key] = round(variance ** 0.5, 2)
        return result

    return {
        'last_5': _averages(logs[:5]),
        'last_10': _averages(logs[:10]),
        'season': _averages(logs),
        'std_dev': _std_devs(logs[:10]),
        'games_played': len(logs),
    }


def update_player_logs_for_games(games: list) -> int:
    """Fetch and cache game logs for all players on tonight's slate.

    *games* is the list of game dicts from ``get_todays_games()``.
    Returns the number of players updated.
    """
    from app.services.nba_service import fetch_player_props_for_event

    player_names = set()
    for game in games:
        event_id = game.get('odds_event_id', '')
        if not event_id:
            continue
        try:
            props = fetch_player_props_for_event(event_id)
        except Exception:
            continue
        for market_props in props.values():
            for prop in market_props:
                player_names.add(prop.get('player', ''))

    count = 0
    for pname in player_names:
        if not pname:
            continue
        pid = find_player_id(pname)
        if not pid:
            logger.warning("Could not resolve player ID for: %s", pname)
            continue

        logs = fetch_player_game_logs(pid, last_n=15)
        if logs:
            cache_player_logs(pid, logs)
            count += 1

    return count


def prune_expired_cache():
    """Remove cached game log rows whose TTL has passed."""
    now = datetime.now(timezone.utc)
    deleted = PlayerGameLog.query.filter(
        PlayerGameLog.cache_expires.isnot(None),
        PlayerGameLog.cache_expires < now,
    ).delete()
    db.session.commit()
    logger.info("Pruned %d expired player game log cache rows", deleted)
    return deleted
