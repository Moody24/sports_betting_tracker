"""Player stats ingestion service using NBA API.

Fetches and caches player game logs for players on tonight's slate.
The NBA API is the primary data warehouse; the database is a thin cache
that holds only active-slate player data.
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta, date as date_type
from difflib import SequenceMatcher
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from app.models import PlayerGameLog
from app.services.nba_service import ESPN_SUMMARY_URL, fetch_espn_scoreboard

logger = logging.getLogger(__name__)

# Rate-limit delay between NBA API calls (seconds)
_NBA_API_DELAY = 0.6
APP_TIMEZONE = ZoneInfo("America/New_York")


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


def fetch_player_game_logs(
    player_id: str,
    season: str = None,
    last_n: Optional[int] = 15,
    raise_on_error: bool = False,
):
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
        if raise_on_error:
            raise
        return []

    if df.empty:
        return []

    rows = []
    frame = df if last_n is None else df.head(last_n)

    for _, row in frame.iterrows():
        game_date = _parse_game_date(row.get('GAME_DATE', ''))
        if game_date is None:
            continue
        matchup = str(row.get('MATCHUP', ''))
        home_away = 'home' if 'vs.' in matchup else 'away'
        rows.append({
            'player_id': str(player_id),
            'player_name': str(row.get('PLAYER_NAME', '')),
            'team_abbr': str(row.get('TEAM_ABBREVIATION', '')),
            'game_date': game_date,
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


def _parse_game_date(date_val) -> Optional[date_type]:
    """Parse a game date from NBA API (various formats).

    Returns None on parse failure so callers can skip the row rather than
    silently stamping logs with today's date.
    """
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
    logger.warning("Could not parse game date %r; skipping row", date_val)
    return None


def _dedupe_logs_by_date(game_logs: list[dict]) -> list[dict]:
    """Deduplicate logs by game_date, keeping the last entry for each date."""
    seen_dates: set[date_type] = set()
    deduped_reversed: list[dict] = []

    for log in reversed(game_logs or []):
        parsed_date = _parse_game_date(log.get('game_date'))
        if parsed_date is None:
            continue
        if parsed_date in seen_dates:
            continue

        normalized_log = dict(log)
        normalized_log['game_date'] = parsed_date
        deduped_reversed.append(normalized_log)
        seen_dates.add(parsed_date)

    deduped_reversed.reverse()
    return deduped_reversed


def _is_postgres() -> bool:
    """Return True when the current DB engine is PostgreSQL."""
    try:
        return db.engine.dialect.name == 'postgresql'
    except Exception:
        return False


def _upsert_player_logs_postgres(player_id: str, rows_dicts: list[dict], expires: datetime) -> None:
    """PostgreSQL race-safe upsert for PlayerGameLog rows."""
    now_utc = datetime.now(timezone.utc)
    rows_to_insert = []
    for log in rows_dicts:
        rows_to_insert.append({
            'player_id': str(player_id),
            'player_name': log.get('player_name', ''),
            'team_abbr': log.get('team_abbr', ''),
            'game_date': log['game_date'],
            'matchup': log.get('matchup', ''),
            'minutes': log.get('minutes', 0),
            'pts': log.get('pts', 0),
            'reb': log.get('reb', 0),
            'ast': log.get('ast', 0),
            'stl': log.get('stl', 0),
            'blk': log.get('blk', 0),
            'tov': log.get('tov', 0),
            'fgm': log.get('fgm', 0),
            'fga': log.get('fga', 0),
            'ftm': log.get('ftm', 0),
            'fta': log.get('fta', 0),
            'fg3m': log.get('fg3m', 0),
            'fg3a': log.get('fg3a', 0),
            'plus_minus': log.get('plus_minus', 0),
            'home_away': log.get('home_away', ''),
            'win_loss': log.get('win_loss', ''),
            'context_flags': log.get('context_flags'),
            'cache_expires': expires,
            'fetched_at': now_utc,
        })

    stmt = pg_insert(PlayerGameLog).values(rows_to_insert)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=['player_id', 'game_date'],
        set_={
            'player_name': excluded.player_name,
            'team_abbr': excluded.team_abbr,
            'matchup': excluded.matchup,
            'minutes': excluded.minutes,
            'pts': excluded.pts,
            'reb': excluded.reb,
            'ast': excluded.ast,
            'stl': excluded.stl,
            'blk': excluded.blk,
            'tov': excluded.tov,
            'fgm': excluded.fgm,
            'fga': excluded.fga,
            'ftm': excluded.ftm,
            'fta': excluded.fta,
            'fg3m': excluded.fg3m,
            'fg3a': excluded.fg3a,
            'plus_minus': excluded.plus_minus,
            'home_away': excluded.home_away,
            'win_loss': excluded.win_loss,
            'context_flags': excluded.context_flags,
            'cache_expires': excluded.cache_expires,
            'fetched_at': excluded.fetched_at,
        },
    )
    db.session.execute(stmt)


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


def cache_player_logs(
    player_id: str,
    game_logs: list,
    ttl_days: int = 7,
    commit: bool = True,
) -> dict:
    """Upsert game logs into the database cache.

    Existing rows for the same player+date are updated; new rows are inserted.
    Sets cache_expires to ``ttl_days`` from now.
    """
    deduped_logs = _dedupe_logs_by_date(game_logs)
    if not deduped_logs:
        return {'inserted': 0, 'updated': 0, 'total': 0}

    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    game_dates = [log['game_date'] for log in deduped_logs]

    for attempt in range(2):
        try:
            with db.session.no_autoflush:
                existing_rows = (
                    PlayerGameLog.query
                    .filter_by(player_id=str(player_id))
                    .filter(PlayerGameLog.game_date.in_(game_dates))
                    .all()
                )

            existing_dates = {row.game_date for row in existing_rows}
            inserted = sum(1 for log in deduped_logs if log['game_date'] not in existing_dates)
            updated = len(deduped_logs) - inserted

            if _is_postgres():
                _upsert_player_logs_postgres(player_id, deduped_logs, expires)
            else:
                existing_map: dict = {row.game_date: row for row in existing_rows}
                for log in deduped_logs:
                    existing = existing_map.get(log['game_date'])

                    if existing:
                        for key, val in log.items():
                            if key not in ('player_id', 'game_date') and hasattr(existing, key):
                                setattr(existing, key, val)
                        existing.cache_expires = expires
                        existing.fetched_at = datetime.now(timezone.utc)
                    else:
                        row = PlayerGameLog(
                            player_id=str(player_id),
                            player_name=log.get('player_name', ''),
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
                        existing_map[log['game_date']] = row

            if commit:
                db.session.commit()

            return {'inserted': inserted, 'updated': updated, 'total': inserted + updated}
        except IntegrityError as exc:
            logger.warning(
                "IntegrityError caching logs for player %s on attempt %d: %s",
                player_id,
                attempt + 1,
                exc,
            )
            db.session.rollback()
            if attempt == 1:
                raise

    return {'inserted': 0, 'updated': 0, 'total': 0}


def get_cached_logs(player_id: str, last_n: int = 15) -> list:
    """Retrieve cached game logs for a player, ordered by date descending.

    Prefers rows whose cache_expires has not yet passed.  If no fresh rows
    exist (e.g. scheduler hasn't run), falls back to all rows with a warning
    so projections degrade gracefully rather than break entirely.
    """
    now = datetime.now(timezone.utc)

    fresh_rows = (
        PlayerGameLog.query
        .filter_by(player_id=str(player_id))
        .filter(
            (PlayerGameLog.cache_expires == None) |  # noqa: E711
            (PlayerGameLog.cache_expires > now)
        )
        .order_by(PlayerGameLog.game_date.desc())
        .limit(last_n)
        .all()
    )
    if fresh_rows:
        return fresh_rows

    # No fresh rows — fall back to all rows and surface the staleness.
    stale_rows = (
        PlayerGameLog.query
        .filter_by(player_id=str(player_id))
        .order_by(PlayerGameLog.game_date.desc())
        .limit(last_n)
        .all()
    )
    if stale_rows:
        logger.warning(
            "get_cached_logs: all logs for player_id=%s are stale (cache expired); "
            "scheduler may not have run recently",
            player_id,
        )
    return stale_rows


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


def _safe_float(value, default=0.0) -> float:
    try:
        return float(str(value).replace("+", "").strip())
    except (ValueError, TypeError):
        return default


def _parse_made_attempt(raw_val) -> tuple[float, float]:
    if raw_val is None:
        return 0.0, 0.0
    text = str(raw_val).strip()
    if '-' not in text:
        val = _safe_float(text, 0.0)
        return val, 0.0
    made, attempted = text.split('-', 1)
    return _safe_float(made, 0.0), _safe_float(attempted, 0.0)


def _extract_stat_value(columns: list[str], stats: list[str], key: str):
    if key not in columns:
        return None
    idx = columns.index(key)
    if idx >= len(stats):
        return None
    return stats[idx]


def _extract_logs_from_espn_summary(summary_data: dict, game: dict, game_date: date_type) -> list[dict]:
    """Parse ESPN summary payload into PlayerGameLog-compatible rows."""
    home = game.get('home', {}) or {}
    away = game.get('away', {}) or {}
    home_name = home.get('name', '')
    away_name = away.get('name', '')
    home_abbr = home.get('abbr', '')
    away_abbr = away.get('abbr', '')
    home_score = int(home.get('score', 0) or 0)
    away_score = int(away.get('score', 0) or 0)

    logs: list[dict] = []
    for team_block in summary_data.get('boxscore', {}).get('players', []):
        team_info = team_block.get('team', {}) or {}
        team_name = team_info.get('displayName', '') or team_info.get('shortDisplayName', '')
        team_abbr = team_info.get('abbreviation', '') or ''

        is_home_team = (team_name and team_name == home_name) or (team_abbr and team_abbr == home_abbr)
        opp_abbr = away_abbr if is_home_team else home_abbr
        home_away = 'home' if is_home_team else 'away'
        matchup = f"{team_abbr} vs. {opp_abbr}" if is_home_team else f"{team_abbr} @ {opp_abbr}"
        win_loss = None
        if home_score != away_score:
            team_won = (home_score > away_score) if is_home_team else (away_score > home_score)
            win_loss = 'W' if team_won else 'L'

        for stat_block in team_block.get('statistics', []):
            columns: list[str] = stat_block.get('names', []) or []
            for athlete in stat_block.get('athletes', []):
                athlete_info = athlete.get('athlete', {}) or {}
                player_name = athlete_info.get('displayName', '') or ''
                if not player_name:
                    continue

                stats: list[str] = athlete.get('stats', []) or []
                min_raw = _extract_stat_value(columns, stats, 'MIN')
                fg_raw = _extract_stat_value(columns, stats, 'FG')
                fg3_raw = _extract_stat_value(columns, stats, '3PT')
                ft_raw = _extract_stat_value(columns, stats, 'FT')
                reb_raw = _extract_stat_value(columns, stats, 'REB')
                ast_raw = _extract_stat_value(columns, stats, 'AST')
                stl_raw = _extract_stat_value(columns, stats, 'STL')
                blk_raw = _extract_stat_value(columns, stats, 'BLK')
                tov_raw = _extract_stat_value(columns, stats, 'TO')
                pm_raw = _extract_stat_value(columns, stats, '+/-')
                pts_raw = _extract_stat_value(columns, stats, 'PTS')
                oreb_raw = _extract_stat_value(columns, stats, 'OREB')
                dreb_raw = _extract_stat_value(columns, stats, 'DREB')

                fgm, fga = _parse_made_attempt(fg_raw)
                fg3m, fg3a = _parse_made_attempt(fg3_raw)
                ftm, fta = _parse_made_attempt(ft_raw)

                reb_val = _safe_float(reb_raw, 0.0)
                if reb_val == 0.0 and (oreb_raw is not None or dreb_raw is not None):
                    reb_val = _safe_float(oreb_raw, 0.0) + _safe_float(dreb_raw, 0.0)

                resolved_player_id = find_player_id(player_name)
                if not resolved_player_id:
                    logger.debug("Skipping unresolved player: %s", player_name)
                    continue

                logs.append({
                    'player_id': str(resolved_player_id),
                    'player_name': player_name,
                    'team_abbr': team_abbr,
                    'game_date': game_date,
                    'matchup': matchup,
                    'minutes': _parse_minutes(min_raw),
                    'pts': _safe_float(pts_raw, 0.0),
                    'reb': reb_val,
                    'ast': _safe_float(ast_raw, 0.0),
                    'stl': _safe_float(stl_raw, 0.0),
                    'blk': _safe_float(blk_raw, 0.0),
                    'tov': _safe_float(tov_raw, 0.0),
                    'fgm': fgm,
                    'fga': fga,
                    'ftm': ftm,
                    'fta': fta,
                    'fg3m': fg3m,
                    'fg3a': fg3a,
                    'plus_minus': _safe_float(pm_raw, 0.0),
                    'home_away': home_away,
                    'win_loss': win_loss,
                })
    return logs


def refresh_completed_game_logs(days_back: int = 2) -> dict:
    """Reliably ingest completed NBA game logs from ESPN summaries.

    Fetches final games for the last ``days_back`` days plus today and
    upserts player rows into PlayerGameLog.
    """
    today_et = datetime.now(APP_TIMEZONE).date()
    inserted = 0
    updated = 0
    players_upserted = 0
    games_seen = 0
    finals_seen = 0

    for offset in range(max(days_back, 0) + 1):
        target_date = today_et - timedelta(days=offset)
        date_str = target_date.strftime('%Y%m%d')
        games = fetch_espn_scoreboard(date_str)
        games_seen += len(games)

        for game in games:
            status = str(game.get('status', '') or '')
            status_detail = str(game.get('status_detail', '') or '').lower()
            if status != 'STATUS_FINAL' and 'final' not in status_detail:
                continue
            finals_seen += 1

            espn_id = game.get('espn_id')
            if not espn_id:
                continue

            try:
                resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id}, timeout=10)
                resp.raise_for_status()
                summary = resp.json()
            except (requests.RequestException, ValueError) as exc:
                logger.error("ESPN summary fetch failed for completed game %s: %s", espn_id, exc)
                continue

            rows = _extract_logs_from_espn_summary(summary, game, target_date)
            if not rows:
                continue

            grouped: dict[str, list] = {}
            for row in rows:
                grouped.setdefault(str(row['player_id']), []).append(row)

            for pid, player_rows in grouped.items():
                try:
                    result = cache_player_logs(pid, player_rows, ttl_days=3650, commit=False)
                    inserted += result['inserted']
                    updated += result['updated']
                    if result['total'] > 0:
                        players_upserted += 1
                except Exception as exc:
                    logger.warning("Failed to cache logs for player %s: %s", pid, exc)
                    db.session.rollback()

    db.session.commit()
    summary = {
        'games_seen': games_seen,
        'final_games_seen': finals_seen,
        'players_upserted': players_upserted,
        'rows_inserted': inserted,
        'rows_updated': updated,
    }
    logger.info("Completed-game log refresh summary: %s", summary)
    return summary


def prune_expired_cache():
    """Remove expired and unresolvable cached game log rows.

    Deletes two categories of dead rows:
    - TTL-expired rows (cache_expires in the past)
    - espn_* pseudo-IDs inserted when find_player_id() previously failed;
      these rows can never be used for projections and are now skipped at
      ingest time, so any that exist from earlier runs should be removed.
    """
    now = datetime.now(timezone.utc)
    expired = PlayerGameLog.query.filter(
        PlayerGameLog.cache_expires.isnot(None),
        PlayerGameLog.cache_expires < now,
    ).delete()

    unresolved = PlayerGameLog.query.filter(
        PlayerGameLog.player_id.like('espn_%'),
    ).delete()

    db.session.commit()
    logger.info(
        "Pruned %d expired and %d unresolved (espn_*) player game log rows",
        expired, unresolved,
    )
    return {'expired': expired, 'unresolved': unresolved}
