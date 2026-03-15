"""Feature engineering for ML models.

Builds feature vectors for both the projection model (Model 1) and the
pick quality classifier (Model 2).
"""

import logging

from app.config_display import PROP_STAT_KEY
from app.services.stats_service import get_cached_logs, get_player_stats_summary
from app.services.matchup_service import (
    get_team_defense,
    get_matchup_adjustment,
    get_pace_factor,
    get_position_matchup_adjustment,
)
from app.services.context_service import check_back_to_back, get_days_rest, get_player_injury_status

logger = logging.getLogger(__name__)


def _append_unique_flag(flags: list[str], flag: str) -> None:
    if flag and flag not in flags:
        flags.append(flag)


def derive_context_flags_from_snapshot(ctx: dict) -> list[str]:
    """Derive stable context flags from a PickContext-like payload."""
    flags: list[str] = []

    matchup_adj = float(ctx.get('opp_matchup_adj', 1.0) or 1.0)
    if matchup_adj > 1.05:
        _append_unique_flag(flags, 'favorable_matchup')
    elif matchup_adj < 0.95:
        _append_unique_flag(flags, 'tough_matchup')

    pos_adj = float(ctx.get('opp_positional_matchup_adj', 1.0) or 1.0)
    if pos_adj > 1.05:
        _append_unique_flag(flags, 'favorable_positional_matchup')
    elif pos_adj < 0.95:
        _append_unique_flag(flags, 'tough_positional_matchup')

    trend = str(ctx.get('player_last5_trend', 'neutral') or 'neutral')
    if trend == 'hot':
        _append_unique_flag(flags, 'hot_streak')
    elif trend == 'cold':
        _append_unique_flag(flags, 'cold_streak')

    pace_factor = float(ctx.get('opp_pace_factor', 1.0) or 1.0)
    if pace_factor > 1.03:
        _append_unique_flag(flags, 'pace_boost')
    elif pace_factor < 0.97:
        _append_unique_flag(flags, 'pace_slowdown')

    if bool(ctx.get('back_to_back', False)):
        _append_unique_flag(flags, 'back_to_back')

    minutes_trend = str(ctx.get('minutes_trend', 'stable') or 'stable')
    if minutes_trend == 'decreasing':
        _append_unique_flag(flags, 'minutes_down')
    elif minutes_trend == 'increasing':
        _append_unique_flag(flags, 'minutes_up')

    if bool(ctx.get('injury_returning', False)):
        _append_unique_flag(flags, 'injury_returning')

    projected_edge = float(ctx.get('projected_edge', 0.0) or 0.0)
    abs_edge = abs(projected_edge)
    if abs_edge >= 0.10:
        _append_unique_flag(flags, 'high_edge')
    elif abs_edge >= 0.05:
        _append_unique_flag(flags, 'medium_edge')

    confidence_tier = str(ctx.get('confidence_tier', '') or '')
    if confidence_tier in {'strong', 'moderate', 'slight'}:
        _append_unique_flag(flags, f'{confidence_tier}_confidence')

    hit_rate = float(ctx.get('player_hit_rate_vs_line', 0.5) or 0.5)
    if hit_rate >= 0.65:
        _append_unique_flag(flags, 'high_hit_rate')
    elif hit_rate <= 0.35:
        _append_unique_flag(flags, 'low_hit_rate')

    line_vs_season_avg = float(ctx.get('line_vs_season_avg', 0.0) or 0.0)
    if line_vs_season_avg <= -2.0:
        _append_unique_flag(flags, 'line_discount')
    elif line_vs_season_avg >= 2.0:
        _append_unique_flag(flags, 'line_premium')

    player_variance = float(ctx.get('player_variance', 0.0) or 0.0)
    if player_variance >= 8.0:
        _append_unique_flag(flags, 'high_variance')
    elif 0 < player_variance <= 3.0:
        _append_unique_flag(flags, 'low_variance')

    days_rest = float(ctx.get('days_rest', 0.0) or 0.0)
    if days_rest >= 3:
        _append_unique_flag(flags, 'extra_rest')

    if not flags:
        flags.append('neutral_context')

    return flags


def build_projection_features(
    player_id: str,
    prop_type: str,
    opponent_name: str = '',
    is_home: bool = True,
    prop_line: float = 0,
) -> dict:
    """Build feature dict for Model 1 (stat projection).

    Features per player-prop prediction:
    - avg_stat_last_5, avg_stat_last_10, avg_stat_season
    - std_stat_last_5, std_stat_last_10
    - min_last_3_avg
    - home_away (binary)
    - back_to_back (binary)
    - days_rest
    - opp_def_rating
    - opp_stat_allowed_vs_position
    - opp_pace
    - games_played_this_season
    - streak_zscore
    - prop_line
    """
    logs = get_cached_logs(player_id, last_n=82)
    summary = get_player_stats_summary(player_id, logs)

    games_played = summary['games_played']

    # Stat averages
    avg_last_5 = _summary_stat_for_prop(summary, prop_type, 'last_5')
    avg_last_10 = _summary_stat_for_prop(summary, prop_type, 'last_10')
    avg_season = _summary_stat_for_prop(summary, prop_type, 'season')

    # Variance
    std_last_5 = _compute_std_for_prop(logs[:5], prop_type)
    std_last_10 = _compute_std_for_prop(logs[:10], prop_type)

    # Minutes trend (last 3)
    min_last_3 = _average_stat(logs[:3], 'minutes')

    # Defense
    defense = get_team_defense(opponent_name) if opponent_name else {}
    def_rating = defense.get('def_rating', 0)
    matchup_adj = get_matchup_adjustment(opponent_name, prop_type) if opponent_name else 1.0
    pace = get_pace_factor(opponent_name) if opponent_name else 1.0

    # Streak z-score
    z_score = _compute_streak_zscore_for_prop(logs, prop_type)

    return {
        'avg_stat_last_5': avg_last_5,
        'avg_stat_last_10': avg_last_10,
        'avg_stat_season': avg_season,
        'std_stat_last_5': std_last_5,
        'std_stat_last_10': std_last_10,
        'min_last_3_avg': min_last_3,
        'home_away': 1 if is_home else 0,
        'back_to_back': 0,  # Filled by caller with team context
        'days_rest': 2,      # Default, filled by caller
        'opp_def_rating': def_rating,
        'opp_stat_allowed': matchup_adj,
        'opp_pace': pace,
        'games_played_this_season': games_played,
        'streak_zscore': z_score,
        'prop_line': prop_line,
    }


def build_pick_context_features(
    player_name: str,
    player_id: str,
    prop_type: str,
    prop_line: float,
    american_odds: int,
    projected_stat: float,
    projected_edge: float,
    confidence_tier: str,
    opponent_name: str = '',
    team_name: str = '',
    is_home: bool = True,
) -> dict:
    """Build the full context snapshot for Model 2 (pick quality classifier).

    This dict is stored as JSON in ``PickContext.context_json`` at bet
    placement time.
    """
    logs = get_cached_logs(player_id, last_n=82)
    summary = get_player_stats_summary(player_id, logs)
    # Player context
    season_avg = _summary_stat_for_prop(summary, prop_type, 'season')
    if prop_type == 'player_points_rebounds_assists':
        std_dev = round((
            float(summary['std_dev'].get('pts', 0) or 0) ** 2 +
            float(summary['std_dev'].get('reb', 0) or 0) ** 2 +
            float(summary['std_dev'].get('ast', 0) or 0) ** 2
        ) ** 0.5, 2)
    else:
        stat_key = _prop_to_stat_key(prop_type)
        std_dev = summary['std_dev'].get(stat_key, 0) if stat_key else 0
    games = summary['games_played']
    player_position = infer_player_position(summary)
    z_score = _compute_streak_zscore_for_prop(logs, prop_type)

    # Determine trend
    if z_score > 1.5:
        trend = 'hot'
    elif z_score < -1.5:
        trend = 'cold'
    else:
        trend = 'neutral'

    # Hit rate vs this line (historical)
    hit_rate = _compute_hit_rate_for_prop(logs, prop_type, prop_line)

    # Minutes trend
    min_last_5 = _average_stat(logs[:5], 'minutes')
    min_season = _average_stat(logs, 'minutes')
    if min_season > 0:
        min_ratio = min_last_5 / min_season
        if min_ratio < 0.85:
            min_trend = 'decreasing'
        elif min_ratio > 1.15:
            min_trend = 'increasing'
        else:
            min_trend = 'stable'
    else:
        min_trend = 'stable'

    # Defense rank (approximate — use def_rating)
    defense = get_team_defense(opponent_name) if opponent_name else {}
    def_rating = defense.get('def_rating', 0)
    pace = defense.get('pace', 0)
    position_pts_allowed = defense.get(f"opp_pts_allowed_{player_position}", 0)

    # Injury returning
    injury = get_player_injury_status(player_name)
    injury_returning = injury.get('status', '') in ('questionable', 'probable')

    # B2B
    b2b = check_back_to_back(team_name) if team_name else False
    raw_days_rest = get_days_rest(team_name) if team_name else 2
    # Fallback: when schedule service misses B2B, infer from <=1 day rest.
    if not b2b and raw_days_rest <= 1:
        b2b = True
    days_rest = 0 if b2b else raw_days_rest

    # Context flags
    matchup_adj = get_matchup_adjustment(opponent_name, prop_type) if opponent_name else 1.0
    position_matchup_adj = (
        get_position_matchup_adjustment(opponent_name, player_position)
        if opponent_name and prop_type == 'player_points'
        else 1.0
    )
    combined_matchup_adj = matchup_adj * position_matchup_adj
    pace_factor = get_pace_factor(opponent_name) if opponent_name else 1.0
    line_vs_season_avg = round(prop_line - season_avg, 1) if season_avg else 0

    context = {
        # Model 1 outputs
        'projected_stat': projected_stat,
        'projected_edge': projected_edge,
        'confidence_tier': confidence_tier,
        'model1_vs_line_diff': round(projected_stat - prop_line, 1),

        # Player context
        'player_last5_trend': trend,
        'player_variance': round(std_dev, 2),
        'player_games_this_season': games,
        'player_position': player_position,
        'player_hit_rate_vs_line': round(hit_rate, 3),

        # Matchup context
        'opp_defense_rating': round(def_rating, 1),
        'opp_pace': round(pace, 1),
        'opp_pts_allowed_vs_position': round(position_pts_allowed, 1) if position_pts_allowed else 0,
        'opp_matchup_adj': round(matchup_adj, 3),
        'opp_positional_matchup_adj': round(position_matchup_adj, 3),
        'opp_combined_matchup_adj': round(combined_matchup_adj, 3),

        # Situational context
        'back_to_back': b2b,
        'home_game': is_home,
        'days_rest': days_rest,
        'minutes_trend': min_trend,
        'injury_returning': injury_returning,

        # Market context
        'prop_line': prop_line,
        'american_odds': american_odds,
        'line_vs_season_avg': line_vs_season_avg,
        'prop_type': prop_type,
        'opp_pace_factor': round(pace_factor, 3),

        # Volatility features — help Model 2 learn which players are high-risk
        # regardless of edge direction.  Computed here at placement time so they
        # are available in PickContext without any postmortem join.
        'minutes_volatility': _compute_std(logs[:20], 'minutes'),
        'stat_attempts_volatility': _compute_attempts_volatility(logs[:20], prop_type),
    }
    context['context_flags'] = derive_context_flags_from_snapshot(context)
    return context



def _summary_stat_for_prop(summary: dict, prop_type: str, bucket: str) -> float:
    if prop_type == 'player_points_rebounds_assists':
        vals = summary.get(bucket, {}) or {}
        return sum(float(vals.get(k, 0) or 0) for k in ('pts', 'reb', 'ast'))
    stat_key = _prop_to_stat_key(prop_type)
    if not stat_key:
        return 0.0
    return float((summary.get(bucket, {}) or {}).get(stat_key, 0) or 0)


def _log_stat_for_prop(log, prop_type: str) -> float:
    if prop_type == 'player_points_rebounds_assists':
        return float((getattr(log, 'pts', 0) or 0) + (getattr(log, 'reb', 0) or 0) + (getattr(log, 'ast', 0) or 0))
    stat_key = _prop_to_stat_key(prop_type)
    if not stat_key:
        return 0.0
    return float(getattr(log, stat_key, 0) or 0)


def _compute_std_for_prop(logs: list, prop_type: str) -> float:
    if len(logs) < 2:
        return 0.0
    vals = [_log_stat_for_prop(lg, prop_type) for lg in logs]
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return round(variance ** 0.5, 2)


def _compute_streak_zscore_for_prop(logs: list, prop_type: str, recent_n: int = 3) -> float:
    if len(logs) < 10:
        return 0.0
    recent_vals = [_log_stat_for_prop(lg, prop_type) for lg in logs[:recent_n]]
    all_vals = [_log_stat_for_prop(lg, prop_type) for lg in logs]
    recent_mean = sum(recent_vals) / len(recent_vals) if recent_vals else 0
    season_mean = sum(all_vals) / len(all_vals) if all_vals else 0
    season_std = ((sum((v - season_mean) ** 2 for v in all_vals) / len(all_vals)) ** 0.5) if all_vals else 0
    if season_std == 0:
        return 0.0
    return round((recent_mean - season_mean) / season_std, 2)


def _compute_hit_rate_for_prop(logs: list, prop_type: str, line: float) -> float:
    if not logs or line <= 0:
        return 0.5
    hits = sum(1 for lg in logs if _log_stat_for_prop(lg, prop_type) > line)
    return hits / len(logs)


def _prop_to_stat_key(prop_type: str) -> str:
    """Map prop market key to internal stat key."""
    return PROP_STAT_KEY.get(prop_type)


def _compute_std(logs: list, stat_key: str) -> float:
    """Compute standard deviation for a stat over a set of logs."""
    if len(logs) < 2:
        return 0.0
    vals = [getattr(lg, stat_key, 0) or 0 for lg in logs]
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return round(variance ** 0.5, 2)


def _average_stat(logs: list, stat_key: str) -> float:
    """Compute average of a stat over a set of logs."""
    if not logs:
        return 0.0
    vals = [getattr(lg, stat_key, 0) or 0 for lg in logs]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def _compute_streak_zscore(logs: list, stat_key: str, recent_n: int = 3) -> float:
    """Calculate z-score of recent games vs season average."""
    if len(logs) < 10:
        return 0.0

    recent_vals = [getattr(lg, stat_key, 0) or 0 for lg in logs[:recent_n]]
    all_vals = [getattr(lg, stat_key, 0) or 0 for lg in logs]

    recent_mean = sum(recent_vals) / len(recent_vals) if recent_vals else 0
    season_mean = sum(all_vals) / len(all_vals) if all_vals else 0
    season_std = (
        (sum((v - season_mean) ** 2 for v in all_vals) / len(all_vals)) ** 0.5
        if all_vals else 0
    )

    if season_std == 0:
        return 0.0

    return round((recent_mean - season_mean) / season_std, 2)


def _compute_hit_rate(logs: list, stat_key: str, line: float) -> float:
    """What percentage of games did the player exceed this line?"""
    if not logs or line <= 0:
        return 0.5

    hits = sum(1 for lg in logs if (getattr(lg, stat_key, 0) or 0) > line)
    return hits / len(logs)


def _compute_attempts_volatility(logs: list, prop_type: str) -> float:
    """Std deviation of shot attempts relevant to this prop type.

    For points props: FGA std dev.
    For threes props: FG3A std dev.
    Returns 0.0 for props where attempts are not applicable.
    """
    from app.services.postmortem_service import PROP_TO_ATTEMPTS_KEY
    attempts_key = PROP_TO_ATTEMPTS_KEY.get(prop_type)
    if not attempts_key:
        return 0.0
    return _compute_std(logs, attempts_key)


def infer_player_position(summary: dict) -> str:
    """Infer a rough position bucket from recent/season stat profile."""
    season = summary.get('season', {}) or {}
    ast = float(season.get('ast', 0) or 0)
    reb = float(season.get('reb', 0) or 0)
    fg3m = float(season.get('fg3m', 0) or 0)

    if reb >= 9.0:
        return 'c'
    if reb >= 7.0 and ast < 4.5:
        return 'pf'
    if ast >= 7.0:
        return 'pg'
    if ast >= 4.5:
        return 'sg'
    if fg3m >= 2.0:
        return 'sf'
    return 'sf'
