"""Value detection and outlier scoring for player props.

Compares the projection engine's output against sportsbook lines to
identify mispriced props and quantify the edge.
"""

import logging
import math
from typing import Optional

from app.services.projection_engine import ProjectionEngine
from app.services.context_service import is_player_available

logger = logging.getLogger(__name__)

# Confidence tier thresholds (edge %)
TIER_STRONG = 0.15
TIER_MODERATE = 0.08
TIER_SLIGHT = 0.03


def implied_prob(american_odds: int) -> float:
    """Convert American odds to implied probability (0..1)."""
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    elif american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100.0)
    return 0.5


def decimal_odds(american_odds: int) -> float:
    """Convert American odds to decimal odds."""
    if american_odds > 0:
        return 1.0 + american_odds / 100.0
    elif american_odds < 0:
        return 1.0 + 100.0 / abs(american_odds)
    return 2.0


class ValueDetector:
    """Identifies value plays by comparing model projections to sportsbook lines."""

    def __init__(self, engine: ProjectionEngine = None):
        self.engine = engine or ProjectionEngine()

    def score_prop(
        self,
        player_name: str,
        prop_type: str,
        line: float,
        over_odds: int,
        under_odds: int,
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
        game_id: str = '',
    ) -> dict:
        """Score a single player prop for value.

        Returns a dict with:
            player: str
            prop_type: str
            line: float
            projection: float
            edge: float           -- model edge (positive = value on over)
            edge_under: float     -- edge for the under side
            recommended_side: str -- 'over' or 'under'
            confidence_tier: str  -- strong/moderate/slight/no_edge
            model_prob_over: float
            book_prob_over: float
            context_notes: list[str]
            std_dev: float
        """
        proj = self.engine.project_stat(
            player_name, prop_type, opponent_name, team_name, is_home,
        )

        projection = proj['projection']
        std_dev = proj['std_dev']
        games_played = proj['games_played']

        if projection == 0 or games_played < 5:
            return self._empty_score(player_name, prop_type, line, over_odds, under_odds, game_id)

        # Model probability of exceeding the line (normal CDF approximation)
        model_prob_over = self._model_prob_over(projection, line, std_dev)
        model_prob_under = 1.0 - model_prob_over

        # Book implied probabilities
        book_prob_over = implied_prob(over_odds) if over_odds else 0.5
        book_prob_under = implied_prob(under_odds) if under_odds else 0.5

        # Edge calculation
        edge_over = model_prob_over - book_prob_over
        edge_under = model_prob_under - book_prob_under

        # Determine recommended side
        if edge_over >= edge_under:
            edge = edge_over
            recommended_side = 'over'
            recommended_odds = over_odds
        else:
            edge = edge_under
            recommended_side = 'under'
            recommended_odds = under_odds

        # Confidence tier
        abs_edge = abs(edge)
        if abs_edge >= TIER_STRONG:
            confidence_tier = 'strong'
        elif abs_edge >= TIER_MODERATE:
            confidence_tier = 'moderate'
        elif abs_edge >= TIER_SLIGHT:
            confidence_tier = 'slight'
        else:
            confidence_tier = 'no_edge'

        return {
            'player': player_name,
            'prop_type': prop_type,
            'line': line,
            'projection': projection,
            'edge': round(edge, 4),
            'edge_over': round(edge_over, 4),
            'edge_under': round(edge_under, 4),
            'recommended_side': recommended_side,
            'recommended_odds': recommended_odds,
            'confidence_tier': confidence_tier,
            'model_prob_over': round(model_prob_over, 4),
            'model_prob_under': round(model_prob_under, 4),
            'book_prob_over': round(book_prob_over, 4),
            'book_prob_under': round(book_prob_under, 4),
            'context_notes': proj.get('context_notes', []),
            'std_dev': round(std_dev, 2),
            'z_score': proj.get('z_score', 0),
            'games_played': games_played,
            'confidence': proj.get('confidence', 'low'),
            'breakdown': proj.get('breakdown', {}),
            'game_id': game_id,
        }

    def _model_prob_over(self, projection: float, line: float, std_dev: float) -> float:
        """Estimate probability of the player exceeding the line.

        Uses the normal distribution CDF.  Falls back to a simple
        comparison if scipy is unavailable.
        """
        if std_dev <= 0:
            return 0.65 if projection > line else 0.35

        try:
            from scipy.stats import norm
            return float(1.0 - norm.cdf(line, loc=projection, scale=std_dev))
        except ImportError:
            # Fallback: approximate normal CDF using the error function
            z = (line - projection) / std_dev
            return 0.5 * (1.0 + math.erf(-z / math.sqrt(2)))

    def _empty_score(self, player, prop_type, line, over_odds, under_odds, game_id=''):
        return {
            'player': player,
            'prop_type': prop_type,
            'line': line,
            'projection': 0,
            'edge': 0,
            'edge_over': 0,
            'edge_under': 0,
            'recommended_side': 'over',
            'recommended_odds': over_odds,
            'confidence_tier': 'no_edge',
            'model_prob_over': 0.5,
            'model_prob_under': 0.5,
            'book_prob_over': implied_prob(over_odds) if over_odds else 0.5,
            'book_prob_under': implied_prob(under_odds) if under_odds else 0.5,
            'context_notes': [],
            'std_dev': 0,
            'z_score': 0,
            'games_played': 0,
            'confidence': 'low',
            'breakdown': {},
            'game_id': game_id,
        }

    def score_all_todays_props(self, games: list = None) -> list:
        """Score all available props across today's NBA games.

        Returns a list of score dicts sorted by absolute edge descending.
        """
        if games is None:
            from app.services.nba_service import get_todays_games
            games = get_todays_games()

        from app.services.nba_service import fetch_player_props_for_event

        all_scores = []

        for game in games:
            event_id = game.get('odds_event_id', '')
            if not event_id:
                continue

            espn_id = game.get('espn_id', '')
            home_team = game.get('home', {}).get('name', '')
            away_team = game.get('away', {}).get('name', '')

            try:
                props = fetch_player_props_for_event(event_id)
            except Exception as exc:
                logger.error("Failed to fetch props for event %s: %s", event_id, exc)
                continue

            for market_key, market_props in props.items():
                for prop in market_props:
                    player = prop.get('player', '')
                    if not player:
                        continue

                    # Skip unavailable players
                    if not is_player_available(player):
                        continue

                    line = prop.get('line', 0)
                    over_odds = prop.get('over_odds', -110)
                    under_odds = prop.get('under_odds', -110)

                    # Determine team/opponent from game context
                    # Heuristic: we don't know the player's team without extra lookup,
                    # so pass both teams and let the engine handle it
                    score = self.score_prop(
                        player_name=player,
                        prop_type=market_key,
                        line=line,
                        over_odds=over_odds,
                        under_odds=under_odds,
                        opponent_name=away_team,
                        team_name=home_team,
                        is_home=True,
                        game_id=espn_id,
                    )

                    # Add game context to score
                    score['home_team'] = home_team
                    score['away_team'] = away_team
                    score['match_date'] = game.get('start_time', '')[:10]

                    all_scores.append(score)

        # Sort by absolute edge descending
        all_scores.sort(key=lambda s: abs(s.get('edge', 0)), reverse=True)

        return all_scores

    def get_top_plays(self, min_edge: float = TIER_SLIGHT, max_plays: int = 20) -> list:
        """Return the top value plays from today's props.

        Filters by minimum edge and excludes no_edge/low confidence plays.
        """
        all_scores = self.score_all_todays_props()
        top = [
            s for s in all_scores
            if abs(s.get('edge', 0)) >= min_edge
            and s.get('confidence_tier') != 'no_edge'
            and s.get('games_played', 0) >= 10
        ]
        return top[:max_plays]


def quarter_kelly(edge: float, american_odds: int, bankroll: float) -> float:
    """Calculate quarter-Kelly bet sizing.

    Returns the recommended stake, capped at 5% of bankroll.
    Returns 0 if edge is non-positive.
    """
    if edge <= 0 or bankroll <= 0:
        return 0.0

    # Decimal payout ratio (excludes stake return)
    if american_odds > 0:
        b = american_odds / 100.0
    elif american_odds < 0:
        b = 100.0 / abs(american_odds)
    else:
        return 0.0

    # Model's true probability
    p = edge + implied_prob(american_odds)
    p = min(max(p, 0.01), 0.99)
    q = 1.0 - p

    # Full Kelly
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0.0

    # Quarter Kelly with 5% bankroll cap
    stake = bankroll * full_kelly * 0.25
    cap = bankroll * 0.05
    return round(min(stake, cap), 2)
