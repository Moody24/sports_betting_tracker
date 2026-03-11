"""Shared ML feature builders for training and inference."""

from __future__ import annotations

from datetime import date as date_type, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from app.config_display import STAT_KEY_TO_OPP_ALLOWED

MIN_TEAM_PLAYERS_FOR_SHARE_FEATURES = 6

# Re-export from centralized config
_STAT_KEY_TO_OPP_ALLOWED = STAT_KEY_TO_OPP_ALLOWED

FEATURE_KEYS = [
    # Original 21 features
    'avg_stat_last_5', 'avg_stat_last_10', 'avg_stat_season', 'std_stat_last_5', 'std_stat_last_10',
    'min_last_3_avg', 'home_away', 'games_played', 'home_split_stat_avg', 'away_split_stat_avg',
    'context_split_stat_avg', 'fg_pct_last_10', 'ts_pct_last_10', 'fga_last_5_avg', 'fg3a_last_5_avg',
    'fg3m_last_5_avg', 'fta_last_5_avg', 'fga_share_last_5', 'pts_share_last_5',
    'usage_share_last_5', 'lead_usage_rate_last_10',
    # Phase 1.1 — situational (3)
    'days_rest', 'back_to_back', 'games_last_7_days',
    # Phase 1.1 — opponent history (2)
    'opp_hist_avg_stat', 'opp_hist_games',
    # Phase 1.1 — game context (1)
    'game_total_line',
    # Phase 1.1 — defensive matchup (3)
    'opp_def_rating', 'opp_pace', 'opp_stat_allowed',
    # Phase 2 — win/loss game-script features (5)
    'avg_stat_in_wins', 'avg_stat_in_losses', 'win_rate_last_10',
    'avg_plus_minus_last_5', 'blowout_rate_last_10',
    # Phase 2 — line movement signal (2)
    'line_delta_today', 'line_movement_available',
]


# ---------------------------------------------------------------------------
# Phase 1.1 helper functions
# ---------------------------------------------------------------------------

def extract_opp_abbr(matchup: str) -> str:
    """Return the opponent team abbreviation from a matchup string.

    Handles both home ('LAL vs. BOS') and away ('LAL @ MIA') formats.
    Returns '' when the string is unrecognised.
    """
    if not matchup:
        return ''
    m = matchup.strip()
    if ' vs. ' in m:
        return m.split(' vs. ', 1)[1].strip().upper()
    if ' @ ' in m:
        return m.split(' @ ', 1)[1].strip().upper()
    return ''


def compute_opp_history(
    prior_logs: Iterable,
    opp_abbr: str,
    stat_key: str,
) -> Tuple[float, int]:
    """Return (avg_stat, game_count) for the player against *opp_abbr*.

    Scans *prior_logs* for games whose matchup resolves to *opp_abbr*.
    Returns (0.0, 0) when there are no matching games or opp_abbr is empty.
    """
    if not opp_abbr:
        return 0.0, 0
    opp_games = [
        g for g in (prior_logs or [])
        if extract_opp_abbr(getattr(g, 'matchup', '') or '') == opp_abbr
    ]
    if not opp_games:
        return 0.0, 0
    vals = [float(getattr(g, stat_key, 0.0) or 0.0) for g in opp_games]
    return (sum(vals) / len(vals)), len(opp_games)


def compute_days_rest_from_logs(
    prior_logs: Iterable,
    current_game_date: Optional[date_type],
) -> float:
    """Return the number of full days between the last game and *current_game_date*.

    Returns 3.0 as a neutral default when date information is unavailable.
    """
    if not current_game_date:
        return 3.0
    dates = [
        getattr(g, 'game_date', None)
        for g in (prior_logs or [])
        if getattr(g, 'game_date', None) is not None
    ]
    if not dates:
        return 3.0
    return float(max(0.0, (current_game_date - max(dates)).days))


def compute_schedule_density(
    prior_logs: Iterable,
    current_game_date: Optional[date_type],
    window_days: int = 7,
) -> int:
    """Return the number of games played in the *window_days* before *current_game_date*."""
    if not current_game_date:
        return 0
    cutoff = current_game_date - timedelta(days=window_days)
    return sum(
        1 for g in (prior_logs or [])
        if getattr(g, 'game_date', None) is not None
        and cutoff <= getattr(g, 'game_date') < current_game_date
    )


def sort_logs_by_date(logs: Iterable, ascending: bool = True) -> List:
    """Return logs sorted by game_date, tolerating missing dates."""

    def _key(log):
        d = getattr(log, 'game_date', None)
        sentinel = date_type.min if ascending else date_type.max
        return (d is None, d or sentinel)

    return sorted(list(logs or []), key=_key, reverse=not ascending)


def build_team_game_aggregates(rows: Iterable) -> Tuple[Dict[tuple, dict], Dict[tuple, int]]:
    """Build per-team/per-date totals and row counts for usage-share features."""
    totals: Dict[tuple, dict] = {}
    counts: Dict[tuple, int] = {}

    for row in rows or []:
        team = (getattr(row, 'team_abbr', '') or '').strip().upper()
        game_date = getattr(row, 'game_date', None)
        if not team or not game_date:
            continue
        key = (team, game_date)
        agg = totals.setdefault(key, {'pts': 0.0, 'fga': 0.0, 'fta': 0.0, 'tov': 0.0})
        agg['pts'] += float(getattr(row, 'pts', 0.0) or 0.0)
        agg['fga'] += float(getattr(row, 'fga', 0.0) or 0.0)
        agg['fta'] += float(getattr(row, 'fta', 0.0) or 0.0)
        agg['tov'] += float(getattr(row, 'tov', 0.0) or 0.0)
        counts[key] = counts.get(key, 0) + 1

    return totals, counts


def compute_team_usage_features_for_player(game_list: Iterable, totals: Dict[tuple, dict], counts: Dict[tuple, int], min_players: int = MIN_TEAM_PLAYERS_FOR_SHARE_FEATURES) -> dict:
    """Compute team share and usage features with completeness gating."""
    games = list(game_list or [])

    def _is_eligible(game) -> bool:
        team = (getattr(game, 'team_abbr', '') or '').strip().upper()
        game_date = getattr(game, 'game_date', None)
        if not team or not game_date:
            return False
        return counts.get((team, game_date), 0) >= int(min_players)

    def _share_avg(game_rows, num_key: str, den_key: str) -> float:
        shares = []
        for game in game_rows:
            if not _is_eligible(game):
                continue
            team = (getattr(game, 'team_abbr', '') or '').strip().upper()
            key = (team, getattr(game, 'game_date', None))
            game_totals = totals.get(key) or {}
            den = float(game_totals.get(den_key, 0.0) or 0.0)
            if den <= 0:
                continue
            shares.append(float(getattr(game, num_key, 0.0) or 0.0) / den)
        return float(sum(shares) / len(shares)) if shares else 0.0

    def _usage_share_avg(game_rows) -> float:
        shares = []
        for game in game_rows:
            if not _is_eligible(game):
                continue
            team = (getattr(game, 'team_abbr', '') or '').strip().upper()
            key = (team, getattr(game, 'game_date', None))
            game_totals = totals.get(key) or {}
            team_usage = float(game_totals.get('fga', 0.0) or 0.0) + 0.44 * float(game_totals.get('fta', 0.0) or 0.0) + float(game_totals.get('tov', 0.0) or 0.0)
            if team_usage <= 0:
                continue
            player_usage = float(getattr(game, 'fga', 0.0) or 0.0) + 0.44 * float(getattr(game, 'fta', 0.0) or 0.0) + float(getattr(game, 'tov', 0.0) or 0.0)
            shares.append(player_usage / team_usage)
        return float(sum(shares) / len(shares)) if shares else 0.0

    def _lead_rate(game_rows, threshold: float = 0.22) -> float:
        leaders = 0
        valid = 0
        for game in game_rows:
            if not _is_eligible(game):
                continue
            team = (getattr(game, 'team_abbr', '') or '').strip().upper()
            key = (team, getattr(game, 'game_date', None))
            game_totals = totals.get(key) or {}
            team_fga = float(game_totals.get('fga', 0.0) or 0.0)
            if team_fga <= 0:
                continue
            valid += 1
            share = float(getattr(game, 'fga', 0.0) or 0.0) / team_fga
            if share >= threshold:
                leaders += 1
        return float(leaders / valid) if valid else 0.0

    sorted_games = sort_logs_by_date(games, ascending=True)
    last_5 = sorted_games[-5:]
    last_10 = sorted_games[-10:]

    return {
        'fga_share_last_5': _share_avg(last_5, 'fga', 'fga'),
        'pts_share_last_5': _share_avg(last_5, 'pts', 'pts'),
        'usage_share_last_5': _usage_share_avg(last_5),
        'lead_usage_rate_last_10': _lead_rate(last_10),
    }


def compute_win_loss_features(logs: List, stat_key: str) -> dict:
    """Compute game-script split features from win_loss and plus_minus fields.

    Returns avg stat in wins, avg stat in losses, win rate over last 10,
    avg plus/minus over last 5, and blowout rate over last 10.
    """
    sorted_logs = sort_logs_by_date(logs, ascending=True)
    last_5 = sorted_logs[-5:]
    last_10 = sorted_logs[-10:]

    def _avg_stat_in_outcome(game_list, outcome_char: str) -> float:
        vals = [
            float(getattr(g, stat_key, 0.0) or 0.0)
            for g in game_list
            if (getattr(g, 'win_loss', '') or '').upper() == outcome_char
        ]
        return float(sum(vals) / len(vals)) if vals else 0.0

    def _win_rate(game_list) -> float:
        total = len(game_list)
        if not total:
            return 0.0
        wins = sum(1 for g in game_list if (getattr(g, 'win_loss', '') or '').upper() == 'W')
        return float(wins / total)

    def _avg_plus_minus(game_list) -> float:
        vals = [float(getattr(g, 'plus_minus', 0.0) or 0.0) for g in game_list]
        return float(sum(vals) / len(vals)) if vals else 0.0

    def _blowout_rate(game_list, threshold: float = 12.0) -> float:
        total = len(game_list)
        if not total:
            return 0.0
        blowouts = sum(
            1 for g in game_list
            if abs(float(getattr(g, 'plus_minus', 0.0) or 0.0)) >= threshold
        )
        return float(blowouts / total)

    return {
        'avg_stat_in_wins':    _avg_stat_in_outcome(sorted_logs, 'W'),
        'avg_stat_in_losses':  _avg_stat_in_outcome(sorted_logs, 'L'),
        'win_rate_last_10':    _win_rate(last_10),
        'avg_plus_minus_last_5': _avg_plus_minus(last_5),
        'blowout_rate_last_10':  _blowout_rate(last_10),
    }


def build_ml_features_from_history(
    prior_logs: Iterable,
    current_is_home: bool,
    stat_key: str,
    all_history_logs: Iterable = None,
    team_totals: Dict[tuple, dict] = None,
    team_counts: Dict[tuple, int] = None,
    # Phase 1.1 context — all optional so callers that don't supply them get
    # safe neutral defaults rather than errors.
    current_game_date: Optional[date_type] = None,
    current_matchup: str = '',
    game_total_line: float = 0.0,
    defense_lookup: Optional[Dict[str, dict]] = None,
    # Phase 2 — line movement signal (0.0 = no data available)
    line_delta: float = 0.0,
) -> dict:
    """Canonical feature builder for model training and live inference."""
    logs = sort_logs_by_date(prior_logs, ascending=True)
    if not logs:
        return {k: 0.0 for k in FEATURE_KEYS}

    last_5 = logs[-5:]
    last_10 = logs[-10:]

    def _avg(game_list, key):
        vals = [float(getattr(g, key, 0.0) or 0.0) for g in game_list]
        return float(sum(vals) / len(vals)) if vals else 0.0

    def _std(game_list, key):
        vals = [float(getattr(g, key, 0.0) or 0.0) for g in game_list]
        if len(vals) < 2:
            return 0.0
        mean = sum(vals) / len(vals)
        return float((sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5)

    def _sum(game_list, key):
        return float(sum(float(getattr(g, key, 0.0) or 0.0) for g in game_list))

    def _ratio_sum(game_list, num_key, den_key):
        den = _sum(game_list, den_key)
        return (_sum(game_list, num_key) / den) if den > 0 else 0.0

    def _true_shooting_pct(game_list):
        denom = 2 * (_sum(game_list, 'fga') + 0.44 * _sum(game_list, 'fta'))
        return (_sum(game_list, 'pts') / denom) if denom > 0 else 0.0

    home_logs = [g for g in logs if (getattr(g, 'home_away', '') or '').lower() == 'home']
    away_logs = [g for g in logs if (getattr(g, 'home_away', '') or '').lower() == 'away']
    context_logs = home_logs if current_is_home else away_logs

    totals = team_totals
    counts = team_counts
    if totals is None or counts is None:
        source_rows = all_history_logs if all_history_logs is not None else logs
        totals, counts = build_team_game_aggregates(source_rows)

    usage_features = compute_team_usage_features_for_player(logs, totals, counts)

    features = {
        'avg_stat_last_5': _avg(last_5, stat_key),
        'avg_stat_last_10': _avg(last_10, stat_key),
        'avg_stat_season': _avg(logs, stat_key),
        'std_stat_last_5': _std(last_5, stat_key),
        'std_stat_last_10': _std(last_10, stat_key),
        'min_last_3_avg': _avg(logs[-3:], 'minutes'),
        'home_away': 1.0 if current_is_home else 0.0,
        'games_played': float(len(logs)),
        'home_split_stat_avg': _avg(home_logs, stat_key),
        'away_split_stat_avg': _avg(away_logs, stat_key),
        'context_split_stat_avg': _avg(context_logs, stat_key),
        'fg_pct_last_10': _ratio_sum(last_10, 'fgm', 'fga'),
        'ts_pct_last_10': _true_shooting_pct(last_10),
        'fga_last_5_avg': _avg(last_5, 'fga'),
        'fg3a_last_5_avg': _avg(last_5, 'fg3a'),
        'fg3m_last_5_avg': _avg(last_5, 'fg3m'),
        'fta_last_5_avg': _avg(last_5, 'fta'),
    }
    features.update(usage_features)

    # ------------------------------------------------------------------
    # Phase 1.1 — situational features (from game logs, no new API)
    # ------------------------------------------------------------------
    _rest = compute_days_rest_from_logs(logs, current_game_date)
    features['days_rest'] = _rest
    features['back_to_back'] = 1.0 if _rest <= 1.0 else 0.0
    features['games_last_7_days'] = float(compute_schedule_density(logs, current_game_date))

    # ------------------------------------------------------------------
    # Phase 1.1 — opponent history (how the player has performed vs opp)
    # ------------------------------------------------------------------
    opp_abbr = extract_opp_abbr(current_matchup or '')
    opp_avg, opp_cnt = compute_opp_history(logs, opp_abbr, stat_key)
    features['opp_hist_avg_stat'] = opp_avg
    features['opp_hist_games'] = float(opp_cnt)

    # ------------------------------------------------------------------
    # Phase 1.1 — game context
    # ------------------------------------------------------------------
    features['game_total_line'] = float(game_total_line or 0.0)

    # ------------------------------------------------------------------
    # Phase 1.1 — defensive matchup (from TeamDefenseSnapshot lookup)
    # ------------------------------------------------------------------
    def_rating, opp_pace, opp_stat_allowed = 0.0, 0.0, 0.0
    if defense_lookup and opp_abbr:
        def_data = defense_lookup.get(opp_abbr, {})
        def_rating = float(def_data.get('def_rating', 0.0) or 0.0)
        opp_pace = float(def_data.get('pace', 0.0) or 0.0)
        allowed_field = _STAT_KEY_TO_OPP_ALLOWED.get(stat_key, 'opp_pts_pg')
        opp_stat_allowed = float(def_data.get(allowed_field, 0.0) or 0.0)
    features['opp_def_rating'] = def_rating
    features['opp_pace'] = opp_pace
    features['opp_stat_allowed'] = opp_stat_allowed

    # ------------------------------------------------------------------
    # Phase 2 — win/loss game-script features
    # ------------------------------------------------------------------
    features.update(compute_win_loss_features(logs, stat_key))

    # ------------------------------------------------------------------
    # Phase 2 — line movement signal
    # ------------------------------------------------------------------
    features['line_delta_today'] = float(line_delta)
    features['line_movement_available'] = 1.0 if line_delta != 0.0 else 0.0

    return features
