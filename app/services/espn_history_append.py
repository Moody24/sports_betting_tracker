"""Append a final game's player box score to HistoricalGameLog (ESPN source).

Same id namespace and mapping conventions as app/cli/hoopr_import.py.
No-refetch guard: if rows for the game already exist, no network call is
made. All failures log and return 0 — callers (the game-day coordinator)
retry naturally on their next tick.
"""

import logging
from datetime import datetime

import requests

from app import db
from app.cli.history_commands import _norm_player_id, _safe_float, _safe_str
from app.models import HistoricalGameLog
from app.services.espn_mapping import (
    NBA_TEAMS, normalize_abbr, season_for_date, usage_pct,
)
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

ESPN_SUMMARY_URL = (
    'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary')

# summary stat label → (stats-payload key, parser)
_SPLIT = lambda made_att, part: _safe_float(made_att.split('-')[part])  # noqa: E731


def history_rows_exist(espn_game_id: str) -> bool:
    return db.session.query(
        HistoricalGameLog.query.filter_by(
            sport='nba', game_id=str(espn_game_id)).exists()
    ).scalar()


def _fetch_summary(espn_id: str) -> dict:
    resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id},
                        timeout=15)
    resp.raise_for_status()
    return resp.json()


def _player_records(payload: dict) -> list[dict]:
    """Flatten summary JSON to per-player dicts with raw float stats."""
    records = []
    for team_block in payload.get('boxscore', {}).get('players', []):
        abbr = normalize_abbr(
            _safe_str(team_block.get('team', {}).get('abbreviation')))
        stats_block = (team_block.get('statistics') or [{}])[0]
        labels = stats_block.get('labels') or []
        idx = {label: i for i, label in enumerate(labels)}

        def col(stats, label, default=0.0):
            i = idx.get(label)
            return _safe_float(stats[i]) if i is not None and i < len(stats) \
                else default

        for ath in stats_block.get('athletes', []):
            stats = ath.get('stats') or []
            if ath.get('didNotPlay') or not stats:
                continue
            fg = stats[idx['FG']] if 'FG' in idx else '0-0'
            fg3 = stats[idx['3PT']] if '3PT' in idx else '0-0'
            ft = stats[idx['FT']] if 'FT' in idx else '0-0'
            pm_raw = stats[idx['+/-']] if '+/-' in idx else '0'
            records.append({
                'player_id': _norm_player_id(ath.get('athlete', {}).get('id')),
                'player_name': _safe_str(
                    ath.get('athlete', {}).get('displayName')),
                'team_abbr': abbr,
                'starter': bool(ath.get('starter')),
                'minutes': col(stats, 'MIN'),
                'pts': col(stats, 'PTS'), 'reb': col(stats, 'REB'),
                'ast': col(stats, 'AST'), 'stl': col(stats, 'STL'),
                'blk': col(stats, 'BLK'), 'tov': col(stats, 'TO'),
                'fgm': _SPLIT(fg, 0), 'fga': _SPLIT(fg, 1),
                'fg3m': _SPLIT(fg3, 0), 'fg3a': _SPLIT(fg3, 1),
                'ftm': _SPLIT(ft, 0), 'fta': _SPLIT(ft, 1),
                'plus_minus': _safe_float(
                    str(pm_raw).replace('+', '') or None),
            })
    return records


def append_final_game(game: dict) -> int:
    """Insert HistoricalGameLog rows for one final scoreboard game dict."""
    espn_id = str(game.get('espn_id') or '')
    if not espn_id:
        return 0
    home_abbr = normalize_abbr(_safe_str(game.get('home', {}).get('abbr')))
    away_abbr = normalize_abbr(_safe_str(game.get('away', {}).get('abbr')))
    if home_abbr not in NBA_TEAMS or away_abbr not in NBA_TEAMS:
        logger.info("history-append: %s skipped (non-NBA teams %s/%s)",
                    espn_id, home_abbr, away_abbr)
        return 0
    if history_rows_exist(espn_id):
        return 0                                  # no-refetch guard

    try:
        payload = _fetch_summary(espn_id)
        records = _player_records(payload)
    except Exception as exc:
        logger.warning("history-append: %s fetch/parse failed: %s",
                       espn_id, exc)
        return 0
    if not records:
        return 0

    try:
        game_date = datetime.fromisoformat(
            game.get('start_time', '').replace('Z', '+00:00')
        ).astimezone(ET).date()
    except (ValueError, TypeError, AttributeError):
        logger.warning("history-append: %s bad start_time %r",
                       espn_id, game.get('start_time'))
        return 0

    try:
        season = season_for_date(game_date)
        home_score = int(game.get('home', {}).get('score') or 0)
        away_score = int(game.get('away', {}).get('score') or 0)

        totals = {}
        for rec in records:
            t = totals.setdefault(rec['team_abbr'],
                                  {'minutes': 0.0, 'fga': 0.0, 'fta': 0.0,
                                   'tov': 0.0})
            for key in t:
                t[key] += rec[key]

        rows = []
        for rec in records:
            team, is_home = rec['team_abbr'], rec['team_abbr'] == home_abbr
            won = (home_score > away_score) if is_home else \
                  (away_score > home_score)
            t = totals[team]
            stats = {k: rec[k] for k in
                     ('pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'fgm', 'fga',
                      'fg3m', 'fg3a', 'ftm', 'fta', 'minutes', 'plus_minus')}
            stats['usage_pct'] = usage_pct(
                rec['fga'], rec['fta'], rec['tov'], rec['minutes'],
                t['minutes'], t['fga'], t['fta'], t['tov'])
            stats['team_score'] = float(home_score if is_home else away_score)
            stats['opp_score'] = float(away_score if is_home else home_score)
            rows.append(HistoricalGameLog(
                sport='nba', player_id=rec['player_id'],
                player_name=rec['player_name'], team_abbr=team,
                opp_abbr=away_abbr if is_home else home_abbr,
                game_id=espn_id, game_date=game_date, season=season,
                home_away='HOME' if is_home else 'AWAY',
                win_loss='W' if won else 'L',
                starter=rec['starter'], stats=stats,
            ))
        db.session.add_all(rows)
        db.session.commit()
        logger.info("history-append: %s +%d rows", espn_id, len(rows))
        return len(rows)
    except Exception as exc:
        db.session.rollback()
        logger.warning("history-append: %s insert failed: %s", espn_id, exc)
        return 0
