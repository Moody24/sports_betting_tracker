"""Player prop projection engine.

Generates projected stat lines using a weighted combination of recent
performance, seasonal averages, matchup context, and situational modifiers.
"""

import logging
import math
from typing import Optional

from app.models import PlayerGameLog
from app.services.stats_service import (
    get_cached_logs,
    get_player_stats_summary,
    find_player_id,
    name_resolver,
)
from app.services.matchup_service import get_matchup_adjustment, get_pace_factor
from app.services.context_service import get_game_context

logger = logging.getLogger(__name__)

# Stat type mapping: prop market key -> internal stat key
PROP_STAT_MAP = {
    'player_points': 'pts',
    'player_rebounds': 'reb',
    'player_assists': 'ast',
    'player_threes': 'fg3m',
    'player_steals': 'stl',
    'player_blocks': 'blk',
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

    def project_stat(
        self,
        player_name: str,
        prop_type: str,
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
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
        stat_key = PROP_STAT_MAP.get(prop_type)
        if not stat_key:
            return self._empty_projection()

        # Look up player and fetch cached logs
        player_id = find_player_id(player_name)
        if not player_id:
            return self._empty_projection()

        logs = get_cached_logs(player_id, last_n=82)
        if not logs:
            return self._empty_projection()

        summary = get_player_stats_summary(player_id, logs)
        games_played = summary['games_played']

        last_5_avg = summary['last_10'].get(stat_key, 0) if games_played < 5 else summary['last_5'].get(stat_key, 0)
        last_10_avg = summary['season'].get(stat_key, 0) if games_played < 10 else summary['last_10'].get(stat_key, 0)
        season_avg = summary['season'].get(stat_key, 0)
        std_dev = summary['std_dev'].get(stat_key, 0)

        # Matchup adjustment
        matchup_mult = get_matchup_adjustment(opponent_name, prop_type) if opponent_name else 1.0
        pace_mult = get_pace_factor(opponent_name) if opponent_name else 1.0
        matchup_adjusted = season_avg * matchup_mult * pace_mult

        # Weighted base projection
        base_projection = (
            self.W_LAST_5 * last_5_avg +
            self.W_LAST_10 * last_10_avg +
            self.W_SEASON * season_avg +
            self.W_MATCHUP * matchup_adjusted
        )

        # Context modifiers
        context_notes = []
        modifier = 1.0

        # Game context
        if team_name:
            ctx = get_game_context(player_name, team_name)

            if ctx.get('back_to_back'):
                modifier *= self.B2B_FACTOR
                context_notes.append('back-to-back (-8%)')

            if ctx.get('injury_status') not in ('healthy', ''):
                if ctx['injury_status'] in ('questionable', 'probable'):
                    context_notes.append(f"injury: {ctx['injury_status']}")
        else:
            ctx = {}

        # Home/away
        if is_home:
            modifier *= self.HOME_BOOST
            context_notes.append('home court (+3%)')
        else:
            modifier *= self.AWAY_PENALTY
            context_notes.append('away game (-3%)')

        # Minutes trend
        min_summary = summary['last_5'].get('minutes', 0) if games_played >= 5 else 0
        min_season = summary['season'].get('minutes', 0)
        if min_season > 0 and min_summary > 0:
            min_ratio = min_summary / min_season
            if min_ratio < 0.85:
                modifier *= 0.90
                context_notes.append('minutes decreasing')
            elif min_ratio > 1.15:
                modifier *= 1.05
                context_notes.append('minutes increasing')

        # Hot/cold streak detection
        z_score = self._compute_z_score(logs, stat_key, last_n=3)
        if z_score > self.HOT_STREAK_THRESHOLD:
            context_notes.append('hot streak')
        elif z_score < self.COLD_STREAK_THRESHOLD:
            context_notes.append('cold streak')
            cold_reasons = self._explain_cold_streak(logs, stat_key)
            context_notes.extend(cold_reasons)

        # Matchup context note
        if opponent_name and matchup_mult > 1.05:
            context_notes.append(f'favorable matchup vs {opponent_name}')
        elif opponent_name and matchup_mult < 0.95:
            context_notes.append(f'tough matchup vs {opponent_name}')

        if opponent_name and pace_mult > 1.03:
            context_notes.append('pace boost')
        elif opponent_name and pace_mult < 0.97:
            context_notes.append('slow pace')

        final_projection = round(base_projection * modifier, 1)

        # Confidence based on sample size and variance
        confidence = self._compute_confidence(games_played, std_dev, season_avg)

        return {
            'projection': final_projection,
            'confidence': confidence,
            'context_notes': context_notes,
            'std_dev': round(std_dev, 2),
            'z_score': round(z_score, 2),
            'games_played': games_played,
            'breakdown': {
                'last_5_avg': round(last_5_avg, 1),
                'last_10_avg': round(last_10_avg, 1),
                'season_avg': round(season_avg, 1),
                'matchup_adj': round(matchup_adjusted, 1),
                'matchup_mult': round(matchup_mult, 3),
                'pace_mult': round(pace_mult, 3),
                'modifier': round(modifier, 3),
                'base_projection': round(base_projection, 1),
            },
        }

    def _compute_z_score(self, logs: list, stat_key: str, last_n: int = 3) -> float:
        """Calculate z-score of recent games vs season average."""
        if len(logs) < 10:
            return 0.0

        recent_vals = [getattr(l, stat_key, 0) or 0 for l in logs[:last_n]]
        all_vals = [getattr(l, stat_key, 0) or 0 for l in logs]

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
            season_mins = sum(getattr(l, 'minutes', 0) or 0 for l in logs) / max(len(logs), 1)
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

    def _empty_projection(self) -> dict:
        return {
            'projection': 0,
            'confidence': 'low',
            'context_notes': [],
            'std_dev': 0,
            'z_score': 0,
            'games_played': 0,
            'breakdown': {},
        }

    def project_all_props_for_player(
        self,
        player_name: str,
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
    ) -> dict:
        """Project all stat types for a player.

        Returns {prop_type: projection_dict}.
        """
        results = {}
        for prop_type in PROP_STAT_MAP:
            results[prop_type] = self.project_stat(
                player_name, prop_type, opponent_name, team_name, is_home,
            )
        return results
