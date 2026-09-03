"""Player prop projection engine.

Generates projected stat lines using a weighted combination of recent
performance, seasonal averages, matchup context, and situational modifiers.
"""

import logging
import math
import os
import time as _time
from copy import deepcopy
from datetime import date as _date, timedelta
from typing import Optional

from app.models import PlayerGameLog
from app.services.stats_service import (
    get_cached_logs,
    get_player_stats_summary,
    find_player_id,
)
from app.services.matchup_service import (
    get_matchup_adjustment,
    get_pace_factor,
    get_position_matchup_adjustment,
)
from app.services.context_service import get_game_context
from app.services.feature_engine import infer_player_position
from app.services.ml_feature_builder import (
    build_ml_features_from_history,
    build_team_game_aggregates,
    compute_team_usage_features_for_player,
)

from app.config_display import PROP_STAT_KEY

logger = logging.getLogger(__name__)

COMBO_PROP_COMPONENTS = {
    'player_points_rebounds_assists': (
        'player_points',
        'player_rebounds',
        'player_assists',
    ),
}

# Calibration corrections for combo props derived by summing individual
# projections. Summing pts+reb+ast underestimates PRA because it ignores
# the positive correlation between components (high-usage games produce
# more of all three). Derived from N=57 postmortem observations after
# retroactive DNP cleanup: avg_err +4.01, over_rate 66.7%.
# Correction = ~80% of observed structural bias (conservative; avoids overfit).
COMBO_PROP_BIAS_CORRECTION = {
    'player_points_rebounds_assists': 3.2,
}

# Additive calibration corrections for single-stat props derived from
# postmortem bias analysis. Applied after both heuristic and ML projections
# to correct systematic model underestimation.
# Updated: N=57 rebounds (avg_err +0.36), N=32 assists (avg_err +0.69).
# Blocks (N=12) and steals (N=10) sample too small — deferred.
SINGLE_STAT_BIAS_CORRECTION = {
    'player_assists': 0.5,
    'player_rebounds': 0.3,
    # revisit threshold when N > 80 — current value calibrated on small sample
}


class ProjectionEngine:
    """Generates projected stat values for player props.

    Projection methodology (weighted average with context adjustments):
      projected = 0.45 * last_5 + 0.30 * last_10 + 0.15 * season + 0.10 * matchup_adj

    Then applies multiplicative context modifiers for situational factors.
    """

    # Weighting constants
    W_LAST_5 = 0.45
    W_LAST_10 = 0.30
    W_SEASON = 0.15
    W_MATCHUP = 0.10

    # Context modifier constants
    B2B_FACTOR = 0.92
    HOME_BOOST = 1.03
    AWAY_PENALTY = 0.97
    INJURY_RETURN_FACTOR = 0.90
    HOT_STREAK_THRESHOLD = 1.5
    COLD_STREAK_THRESHOLD = -1.5

    def __init__(self):
        # Request-local memoization for analysis endpoints.
        # A fresh engine instance is created per request, so this does not
        # introduce cross-request staleness.
        self._projection_cache = {}
        self._player_state_cache = {}
        self._context_cache = {}

    def project_stat(
        self,
        player_name: str,
        prop_type: str,
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
        game_total_line: float = 0.0,
        game_date: Optional[_date] = None,
    ) -> dict:
        """Generate a projection for a single player-stat combination.

        Returns a dict with:
            projection: float  -- the projected stat value
            confidence: str    -- low/medium/high
            context_notes: list[str]
            std_dev: float
            z_score: float     -- hot/cold streak indicator
            breakdown: dict    -- component projections for transparency
        """
        cache_key = (
            str(player_name).strip().lower(),
            str(prop_type).strip().lower(),
            str(opponent_name).strip().lower(),
            str(team_name).strip().lower(),
            bool(is_home),
            float(game_total_line or 0.0),
            game_date,
        )
        if cache_key in self._projection_cache:
            return deepcopy(self._projection_cache[cache_key])

        components = COMBO_PROP_COMPONENTS.get(prop_type)
        if components:
            result = self._project_combo(
                player_name=player_name,
                prop_type=prop_type,
                components=components,
                opponent_name=opponent_name,
                team_name=team_name,
                is_home=is_home,
                game_date=game_date,
            )
            self._projection_cache[cache_key] = result
            return deepcopy(result)

        stat_key = PROP_STAT_KEY.get(prop_type)
        if not stat_key:
            return self._empty_projection()

        player_state = self._get_player_state(player_name)
        if player_state is None:
            return self._empty_projection()

        _, logs, summary = player_state
        baseline = self._build_baseline(summary, stat_key, prop_type, opponent_name)
        games_played = baseline['games_played']
        season_avg = baseline['season_avg']
        std_dev = baseline['std_dev']
        matchup_mult = baseline['matchup_mult']
        position_matchup_mult = baseline['position_matchup_mult']
        player_position = baseline['player_position']
        pace_mult = baseline['pace_mult']
        base_projection = baseline['base_projection']

        modifier, context_notes, unavailable = self._context_modifier(
            player_name, team_name, is_home
        )
        if unavailable is not None:
            return unavailable

        minutes_modifier, minutes_note = self._minutes_modifier(
            summary, games_played
        )
        modifier *= minutes_modifier
        if minutes_note:
            context_notes.append(minutes_note)

        z_score = self._compute_z_score(logs, stat_key, last_n=3)
        self._append_streak_notes(context_notes, logs, stat_key, z_score)
        self._append_matchup_notes(
            context_notes,
            opponent_name,
            prop_type,
            player_position,
            matchup_mult,
            position_matchup_mult,
            pace_mult,
        )

        final_projection, projection_source = self._select_projection(
            heuristic_projection=round(base_projection * modifier, 1),
            player_name=player_name,
            prop_type=prop_type,
            games_played=games_played,
            logs=logs,
            stat_key=stat_key,
            is_home=is_home,
            team_name=team_name,
            opponent_name=opponent_name,
            game_total_line=game_total_line,
            game_date=game_date,
        )
        confidence = self._compute_confidence(games_played, std_dev, season_avg)

        # Apply single-stat systematic bias correction (postmortem-derived).
        bias = SINGLE_STAT_BIAS_CORRECTION.get(prop_type, 0)
        if bias:
            final_projection = round(final_projection + bias, 1)

        result = {
            'projection': final_projection,
            'confidence': confidence,
            'context_notes': context_notes,
            'std_dev': round(std_dev, 2),
            'z_score': round(z_score, 2),
            'games_played': games_played,
            'projection_source': projection_source,
            'breakdown': {
                'last_5_avg': round(baseline['last_5_avg'], 1),
                'last_10_avg': round(baseline['last_10_avg'], 1),
                'season_avg': round(season_avg, 1),
                'matchup_adj': round(baseline['matchup_adjusted'], 1),
                'matchup_mult': round(matchup_mult, 3),
                'position_matchup_mult': round(position_matchup_mult, 3),
                'player_position': player_position,
                'pace_mult': round(pace_mult, 3),
                'modifier': round(modifier, 3),
                'base_projection': round(base_projection, 1),
            },
        }
        self._projection_cache[cache_key] = result
        return deepcopy(result)

    def _project_combo(
        self,
        *,
        player_name: str,
        prop_type: str,
        components: tuple[str, ...],
        opponent_name: str,
        team_name: str,
        is_home: bool,
        game_date: Optional[_date],
    ) -> dict:
        component_results = {
            component: self.project_stat(
                player_name,
                component,
                opponent_name,
                team_name,
                is_home,
                game_date=game_date,
            )
            for component in components
        }
        total_projection = sum(
            result.get('projection', 0) or 0
            for result in component_results.values()
        ) + COMBO_PROP_BIAS_CORRECTION.get(prop_type, 0)
        total_variance = sum(
            (result.get('std_dev', 0) or 0) ** 2
            for result in component_results.values()
        )
        context_notes = []
        for component in components:
            for note in component_results[component].get('context_notes', []):
                if note not in context_notes:
                    context_notes.append(note)
        return {
            'projection': round(total_projection, 1),
            'confidence': min(
                (result.get('confidence', 'low')
                 for result in component_results.values()),
                key=lambda confidence: {
                    'low': 0,
                    'medium': 1,
                    'high': 2,
                }.get(confidence, 0),
            ),
            'context_notes': context_notes,
            'std_dev': round(math.sqrt(total_variance), 2),
            'z_score': round(
                sum(result.get('z_score', 0) or 0
                    for result in component_results.values()) / len(components),
                2,
            ),
            'games_played': min(
                result.get('games_played', 0) or 0
                for result in component_results.values()
            ),
            'projection_source': 'derived_combo',
            'breakdown': {
                'components': {
                    key: deepcopy(value)
                    for key, value in component_results.items()
                },
            },
        }

    def _get_player_state(self, player_name: str):
        """Return cached ``(player_id, logs, summary)`` or None without data."""
        cache_key = str(player_name).strip().lower()
        cached = self._player_state_cache.get(cache_key)
        if cached is not None:
            return cached
        player_id = find_player_id(player_name)
        if not player_id:
            return None
        logs = get_cached_logs(player_id, last_n=82)
        if not logs:
            return None
        state = (player_id, logs, get_player_stats_summary(player_id, logs))
        self._player_state_cache[cache_key] = state
        return state

    def _build_baseline(
        self,
        summary: dict,
        stat_key: str,
        prop_type: str,
        opponent_name: str,
    ) -> dict:
        games_played = summary['games_played']
        last_5_avg = (
            summary['last_10'].get(stat_key, 0)
            if games_played < 5 else summary['last_5'].get(stat_key, 0)
        )
        last_10_avg = (
            summary['season'].get(stat_key, 0)
            if games_played < 10 else summary['last_10'].get(stat_key, 0)
        )
        season_avg = summary['season'].get(stat_key, 0)
        matchup_mult = (
            get_matchup_adjustment(opponent_name, prop_type)
            if opponent_name else 1.0
        )
        player_position = infer_player_position(summary)
        position_matchup_mult = (
            get_position_matchup_adjustment(opponent_name, player_position)
            if opponent_name and prop_type == 'player_points' else 1.0
        )
        pace_mult = get_pace_factor(opponent_name) if opponent_name else 1.0
        matchup_adjusted = (
            season_avg * matchup_mult * position_matchup_mult * pace_mult
        )
        base_projection = (
            self.W_LAST_5 * last_5_avg
            + self.W_LAST_10 * last_10_avg
            + self.W_SEASON * season_avg
            + self.W_MATCHUP * matchup_adjusted
        )
        return {
            'games_played': games_played,
            'last_5_avg': last_5_avg,
            'last_10_avg': last_10_avg,
            'season_avg': season_avg,
            'std_dev': summary['std_dev'].get(stat_key, 0),
            'matchup_mult': matchup_mult,
            'position_matchup_mult': position_matchup_mult,
            'player_position': player_position,
            'pace_mult': pace_mult,
            'matchup_adjusted': matchup_adjusted,
            'base_projection': base_projection,
        }

    def _context_modifier(
        self,
        player_name: str,
        team_name: str,
        is_home: bool,
    ) -> tuple[float, list[str], dict | None]:
        modifier = 1.0
        notes = []
        context = self._cached_game_context(player_name, team_name) if team_name else {}
        injury_status = context.get('injury_status', 'healthy')
        if injury_status in ('out', 'doubtful'):
            unavailable = self._empty_projection()
            unavailable['context_notes'] = [
                f'player listed as {injury_status} — no projection'
            ]
            return modifier, notes, unavailable
        if context.get('back_to_back'):
            modifier *= self.B2B_FACTOR
            notes.append('back-to-back (-8%)')
        if injury_status == 'day-to-day':
            modifier *= self.INJURY_RETURN_FACTOR
            notes.append('day-to-day (-10%)')
        elif injury_status in ('questionable', 'probable'):
            notes.append(f'injury: {injury_status}')
        if is_home:
            modifier *= self.HOME_BOOST
            notes.append('home court (+3%)')
        else:
            modifier *= self.AWAY_PENALTY
            notes.append('away game (-3%)')
        return modifier, notes, None

    def _cached_game_context(self, player_name: str, team_name: str) -> dict:
        cache_key = (
            str(player_name).strip().lower(),
            str(team_name).strip().lower(),
        )
        cached = self._context_cache.get(cache_key)
        if cached is not None:
            return cached
        started_at = _time.perf_counter()
        context = get_game_context(player_name, team_name)
        elapsed = _time.perf_counter() - started_at
        if elapsed > 0.05:
            logger.debug(
                'PERF get_game_context player=%s team=%s elapsed=%.3fs',
                player_name,
                team_name,
                elapsed,
            )
        self._context_cache[cache_key] = context
        return context

    @staticmethod
    def _minutes_modifier(summary: dict, games_played: int) -> tuple[float, str]:
        recent_minutes = (
            summary['last_5'].get('minutes', 0) if games_played >= 5 else 0
        )
        season_minutes = summary['season'].get('minutes', 0)
        if season_minutes <= 0 or recent_minutes <= 0:
            return 1.0, ''
        ratio = recent_minutes / season_minutes
        if ratio < 0.85:
            return 0.90, 'minutes decreasing'
        if ratio > 1.15:
            return 1.05, 'minutes increasing'
        return 1.0, ''

    def _append_streak_notes(
        self,
        notes: list[str],
        logs: list,
        stat_key: str,
        z_score: float,
    ) -> None:
        if z_score > self.HOT_STREAK_THRESHOLD:
            notes.append('hot streak')
        elif z_score < self.COLD_STREAK_THRESHOLD:
            notes.append('cold streak')
            notes.extend(self._explain_cold_streak(logs, stat_key))

    @staticmethod
    def _append_matchup_notes(
        notes: list[str],
        opponent_name: str,
        prop_type: str,
        player_position: str,
        matchup_mult: float,
        position_matchup_mult: float,
        pace_mult: float,
    ) -> None:
        if opponent_name and matchup_mult > 1.05:
            notes.append(f'favorable matchup vs {opponent_name}')
        elif opponent_name and matchup_mult < 0.95:
            notes.append(f'tough matchup vs {opponent_name}')
        if prop_type == 'player_points' and opponent_name:
            if position_matchup_mult > 1.05:
                notes.append(f'favorable vs {player_position.upper()} defenders')
            elif position_matchup_mult < 0.95:
                notes.append(f'tough vs {player_position.upper()} defenders')
        if opponent_name and pace_mult > 1.03:
            notes.append('pace boost')
        elif opponent_name and pace_mult < 0.97:
            notes.append('slow pace')

    def _select_projection(
        self,
        *,
        heuristic_projection: float,
        player_name: str,
        prop_type: str,
        games_played: int,
        logs: list,
        stat_key: str,
        is_home: bool,
        team_name: str,
        opponent_name: str,
        game_total_line: float,
        game_date: Optional[_date],
    ) -> tuple[float, str]:
        if not self._use_ml_projections() or games_played < 10:
            return heuristic_projection, 'heuristic'
        defense_lookup = self._get_defense_lookup()
        separator = ' vs. ' if is_home else ' @ '
        current_matchup = (
            f'{team_name}{separator}{opponent_name}'
            if team_name and opponent_name else ''
        )
        features = self._build_ml_features(
            logs,
            stat_key,
            is_home,
            current_matchup=current_matchup,
            game_total_line=game_total_line,
            defense_lookup=defense_lookup,
            game_date=game_date,
        )
        if not features:
            return heuristic_projection, 'heuristic'
        try:
            from app.services.ml_model import predict_stat
            prediction = predict_stat(prop_type, features)
        except Exception as exc:
            logger.warning(
                'ML projection failed for %s (%s); using heuristic fallback: %s',
                player_name,
                prop_type,
                exc,
            )
            return heuristic_projection, 'heuristic'
        return (prediction, 'ml') if prediction > 0 else (heuristic_projection, 'heuristic')

    def _get_defense_lookup(self) -> dict:
        cached = self._context_cache.get('__defense_lookup__')
        if cached is not None:
            return cached
        try:
            from app.services.ml_model import build_defense_lookup
            lookup = build_defense_lookup()
        except Exception:
            lookup = {}
        self._context_cache['__defense_lookup__'] = lookup
        return lookup

    def _compute_z_score(self, logs: list, stat_key: str, last_n: int = 3) -> float:
        """Calculate z-score of recent games vs season average."""
        if len(logs) < 10:
            return 0.0

        logs = sorted(logs, key=lambda x: (getattr(x, 'game_date', None) is None, getattr(x, 'game_date', None) or _date.min))
        recent_vals = [getattr(lg, stat_key, 0) or 0 for lg in logs[-last_n:]]
        all_vals = [getattr(lg, stat_key, 0) or 0 for lg in logs]

        if not recent_vals or not all_vals:
            return 0.0

        recent_mean = sum(recent_vals) / len(recent_vals)
        season_mean = sum(all_vals) / len(all_vals)
        season_std = math.sqrt(
            sum((v - season_mean) ** 2 for v in all_vals) / len(all_vals)
        )

        if season_std == 0:
            return 0.0

        return (recent_mean - season_mean) / season_std

    def _explain_cold_streak(self, logs: list, stat_key: str) -> list:
        """Look for explanatory factors for a cold streak."""
        reasons = []
        recent = logs[:3]

        for log in recent:
            # Blowout check (low minutes)
            season_mins = sum(getattr(lg, 'minutes', 0) or 0 for lg in logs) / max(len(logs), 1)
            if season_mins > 0 and (log.minutes or 0) < season_mins * 0.75:
                reasons.append('recent blowout/low minutes')
                break

        return reasons[:2]

    def _compute_confidence(self, games_played: int, std_dev: float, avg: float) -> str:
        """Determine confidence level based on sample size and variance."""
        if games_played < 10:
            return 'low'

        if avg > 0:
            cv = std_dev / avg
            if cv > 0.5:
                return 'low'
            elif cv > 0.3:
                return 'medium'
        elif std_dev > 5:
            return 'low'

        if games_played >= 30:
            return 'high'
        elif games_played >= 15:
            return 'medium'

        return 'medium'

    def _use_ml_projections(self) -> bool:
        return os.getenv('USE_ML_PROJECTIONS', 'false').lower() == 'true'

    def _build_ml_features(
        self,
        logs: list,
        stat_key: str,
        is_home: bool,
        current_matchup: str = '',
        game_total_line: float = 0.0,
        defense_lookup: dict = None,
        game_date: Optional[_date] = None,
    ) -> dict:
        if len(logs) < 10:
            return {}

        if game_date is None:
            # Caller did not supply the real scheduled date — fall back to the
            # last-log-date + 1 day approximation so existing call sites
            # (analysis routes, CLI) continue to work unchanged.
            last_date = None
            for lg in reversed(logs):
                d = getattr(lg, 'game_date', None)
                if d is not None:
                    last_date = d
                    break
            game_date = (last_date + timedelta(days=1)) if last_date else None

        usage_features = self._compute_team_usage_features(logs)

        return build_ml_features_from_history(
            prior_logs=logs,
            current_is_home=is_home,
            stat_key=stat_key,
            team_totals=usage_features['team_totals'],
            team_counts=usage_features['team_counts'],
            current_game_date=game_date,
            current_matchup=current_matchup,
            game_total_line=game_total_line,
            defense_lookup=defense_lookup,
        )

    def _compute_team_usage_features(self, logs: list) -> dict:
        team_abbr = (getattr(logs[0], 'team_abbr', '') or '').strip().upper() if logs else ''
        if not team_abbr:
            return {'team_totals': {}, 'team_counts': {}}

        sorted_logs = sorted(logs, key=lambda lg: ((getattr(lg, 'game_date', None) is None), getattr(lg, 'game_date', None)))
        dates = {getattr(g, 'game_date', None) for g in sorted_logs[-10:] if getattr(g, 'game_date', None)}
        if not dates:
            return {'team_totals': {}, 'team_counts': {}}

        rows = (
            PlayerGameLog.query
            .filter(PlayerGameLog.team_abbr == team_abbr)
            .filter(PlayerGameLog.game_date.in_(list(dates)))
            .all()
        )

        totals, counts = build_team_game_aggregates(rows)

        # Trigger computation once so this path also uses shared gating logic.
        compute_team_usage_features_for_player(sorted_logs, totals, counts)
        return {'team_totals': totals, 'team_counts': counts}

    def _empty_projection(self) -> dict:
        return {
            'projection': 0,
            'confidence': 'low',
            'context_notes': [],
            'std_dev': 0,
            'z_score': 0,
            'games_played': 0,
            'projection_source': 'heuristic',
            'breakdown': {},
        }

    def project_all_props_for_player(
        self,
        player_name: str,
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
        game_date: Optional[_date] = None,
    ) -> dict:
        """Project all stat types for a player.

        Returns {prop_type: projection_dict}.
        """
        results = {}
        for prop_type in tuple(PROP_STAT_KEY) + tuple(COMBO_PROP_COMPONENTS):
            results[prop_type] = self.project_stat(
                player_name, prop_type, opponent_name, team_name, is_home,
                game_date=game_date,
            )
        return results
