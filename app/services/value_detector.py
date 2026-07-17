"""Value detection and outlier scoring for player props.

Compares the projection engine's output against sportsbook lines to
identify mispriced props and quantify the edge.
"""

import json
import logging
import math
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, datetime
from itertools import combinations
from typing import Optional

from app.config_display import PROP_STAT_KEY
from app.models import PlayerGameLog
from app.services.pick_quality_model import predict_pick_quality
from app.services.projection_engine import ProjectionEngine
from app.services.feature_engine import build_pick_context_features
from app.utils.time_helpers import ET
from app.services.context_service import is_player_available
from app.services.stats_service import find_player_id, get_cached_logs
from app.utils.odds import american_from_decimal, decimal_odds, implied_prob
from app.utils.time_helpers import et_date_str

logger = logging.getLogger(__name__)

# Module-level cache for score_all_todays_props().
# Keyed by ET date string; expires after TTL so stale data never persists.
# Multiple web-request threads share this cache within the same worker process.
_SCORE_CACHE: dict = {}
_SCORE_CACHE_TTL = 600  # 10 minutes — pre-game props rarely change faster than this

# Confidence tier thresholds (edge %)
TIER_STRONG = 0.15
TIER_MODERATE = 0.08
TIER_SLIGHT = 0.03
STRONG_CONFIDENCE_LEVELS = {'medium', 'high'}

SCENARIO_MIN_MATCHES = 5
SCENARIO_STRONG_THRESHOLD = 0.5
PROP_TO_SPLIT_STAT = {
    'player_points': 'pts',
    'player_rebounds': 'reb',
    'player_assists': 'ast',
    'player_threes': 'fg3m',
    'player_points_rebounds_assists': 'pra',
}
_TIER_DEMOTE = {'strong': 'moderate', 'moderate': 'slight', 'slight': 'no_edge'}


def _apply_scenario_nudge(tier: str, agreement: float, matches: int) -> str:
    """One bounded step: strong disagreement demotes; strong agreement can
    only promote slight -> moderate (a scenario signal never manufactures
    'strong')."""
    if matches < SCENARIO_MIN_MATCHES:
        return tier
    if agreement <= -SCENARIO_STRONG_THRESHOLD:
        return _TIER_DEMOTE.get(tier, tier)
    if agreement >= SCENARIO_STRONG_THRESHOLD and tier == 'slight':
        return 'moderate'
    return tier


def _sanitize_context_notes(notes, max_notes: int = 8) -> list[str]:
    """Normalize, dedupe, and cap context notes for stable UI rendering."""
    seen = set()
    cleaned: list[str] = []
    for raw in notes or []:
        note = str(raw or '').strip()
        if not note:
            continue
        key = note.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(note)
        if len(cleaned) >= max_notes:
            break
    return cleaned


def devig_probs(over_odds: int, under_odds: int) -> tuple[float, float]:
    """Convert over/under odds to no-vig probabilities."""
    over_raw = implied_prob(over_odds) if over_odds else 0.5
    under_raw = implied_prob(under_odds) if under_odds else 0.5
    total = over_raw + under_raw
    if total <= 0:
        return 0.5, 0.5
    return over_raw / total, under_raw / total


class ValueDetector:
    """Identifies value plays by comparing model projections to sportsbook lines."""

    def __init__(self, engine: ProjectionEngine = None):
        self.engine = engine or ProjectionEngine()
        # Per-scan scenario-signal cache: non-None only while
        # score_all_todays_props is scoring, so the ScenarioContextPack is
        # fetched once per scan, live context once per (player, game), and
        # 'all'-scope splits once per (player, stat). Standalone score_prop
        # calls (cache is None) query fresh every time.
        self._scenario_scan_cache: Optional[dict] = None

    @staticmethod
    def _build_player_team_map(player_names: set[str]) -> dict[str, str]:
        if not player_names:
            return {}

        resolved: dict[str, str] = {}
        rows = (
            PlayerGameLog.query
            .filter(PlayerGameLog.player_name.in_(list(player_names)))
            .order_by(PlayerGameLog.player_name, PlayerGameLog.game_date.desc())
            .all()
        )
        for row in rows:
            if row.player_name not in resolved and row.team_abbr:
                resolved[row.player_name] = (row.team_abbr or '').upper()

        for player_name in player_names:
            if player_name in resolved:
                continue
            try:
                player_id = find_player_id(player_name)
                if not player_id:
                    continue
                logs = get_cached_logs(player_id, last_n=1)
                if logs and logs[0].team_abbr:
                    resolved[player_name] = (logs[0].team_abbr or '').upper()
            except Exception as exc:
                logger.debug("Could not resolve team for %s: %s", player_name, exc)
                continue
        return resolved

    @staticmethod
    def _resolve_game_context_for_player(
        player_name: str,
        home_team: str,
        away_team: str,
        home_abbr: str,
        away_abbr: str,
        player_team_map: dict[str, str],
    ) -> tuple[str, str, bool]:
        player_team_abbr = player_team_map.get(player_name, '')
        if player_team_abbr and player_team_abbr == home_abbr:
            return home_team, away_team, True
        if player_team_abbr and player_team_abbr == away_abbr:
            return away_team, home_team, False
        # Fallback if unknown team: preserve previous behavior.
        return home_team, away_team, True

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
        game_date: Optional[_date] = None,
        game_total_line: float = 0.0,
        spread: Optional[float] = None,
        favored_side: Optional[str] = None,
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
            game_total_line=game_total_line, game_date=game_date,
        )

        projection = proj['projection']
        std_dev = proj['std_dev']
        games_played = proj['games_played']

        if projection == 0 or games_played < 5:
            return self._empty_score(player_name, prop_type, line, over_odds, under_odds, game_id)

        # Model probability of exceeding the line (calibrated distributional
        # model when USE_DISTRIBUTIONAL_MODEL=true; normal CDF approximation
        # otherwise — see _model_prob_over).
        model_prob_over, dist_point = self._model_prob_over_details(
            projection, line, std_dev,
            player_name=player_name, prop_type=prop_type,
            opponent_name=opponent_name, team_name=team_name,
            is_home=is_home, game_date=game_date, game_total_line=game_total_line,
        )
        model_prob_under = 1.0 - model_prob_over

        # When the distributional head scored this prop, display ITS point
        # estimate so projection and P(over) describe the same distribution
        # (the heuristic projection carries bias corrections the dist head
        # deliberately omits — e.g. PRA's combo correction).
        projection_source = proj.get('projection_source', 'heuristic')
        if dist_point is not None:
            projection = round(float(dist_point), 1)
            projection_source = 'distributional'

        # Book no-vig probabilities
        book_prob_over, book_prob_under = devig_probs(over_odds, under_odds)

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
        projection_confidence = proj.get('confidence', 'low')
        if abs_edge >= TIER_STRONG and projection_confidence in STRONG_CONFIDENCE_LEVELS:
            confidence_tier = 'strong'
        elif abs_edge >= TIER_MODERATE:
            confidence_tier = 'moderate'
        elif abs_edge >= TIER_SLIGHT:
            confidence_tier = 'slight'
        else:
            confidence_tier = 'no_edge'

        # Model 2 (pick quality) enhancement
        context_notes = _sanitize_context_notes(proj.get('context_notes', []))
        win_probability = None
        pick_quality_recommendation = 'no_model'
        try:
            player_id = find_player_id(player_name) or ''
            if player_id:
                qctx = build_pick_context_features(
                    player_name=player_name,
                    player_id=player_id,
                    prop_type=prop_type,
                    prop_line=line,
                    american_odds=recommended_odds,
                    projected_stat=projection,
                    projected_edge=edge,
                    confidence_tier=confidence_tier,
                    opponent_name=opponent_name,
                    team_name=team_name,
                    is_home=is_home,
                )
            else:
                # Fallback when player_id cannot be resolved — minimal context only.
                z = proj.get('z_score', 0)
                trend_str = 'hot' if z > 1.5 else ('cold' if z < -1.5 else 'neutral')
                b2b = any('back-to-back' in note for note in context_notes)
                qctx = {
                    'projected_stat': projection,
                    'projected_edge': edge,
                    'prop_line': line,
                    'player_last5_trend': trend_str,
                    'back_to_back': int(b2b),
                }
            quality = predict_pick_quality(qctx)
            win_prob = quality.get('win_probability', 0.5)
            win_probability = round(win_prob, 3)
            pick_quality_recommendation = quality.get('recommendation', 'no_model')
            # Downgrade moderate → slight if model says <42% win probability
            if win_prob < 0.42 and confidence_tier == 'moderate':
                confidence_tier = 'slight'
            if win_prob >= 0.60:
                context_notes.append(f'ML quality: {win_prob:.0%} win prob')
            elif win_prob < 0.40:
                context_notes.append(f'ML caution: {win_prob:.0%} win prob')
        except Exception as exc:
            logger.warning("Model 2 quality scoring unavailable for %s/%s: %s", player_name, prop_type, exc)
        context_notes = _sanitize_context_notes(context_notes)

        # Scenario-split agreement signal (Plan C Increment 2). Applied after
        # the Model 2 adjustment so the bounded nudge has the last word.
        scenario_agreement = None
        scenario_matches = None
        if self._use_scenario_signal():
            try:
                signal = self._scenario_signal(
                    player_name, prop_type, line, opponent_name, team_name,
                    is_home, game_date, game_total_line, spread, favored_side)
            except Exception as exc:
                logger.warning(
                    "Scenario signal failed for %s/%s: %s",
                    player_name, prop_type, exc)
                signal = None
            if signal is not None:
                scenario_agreement, scenario_matches, pack_fresh = signal
                lean = 'over' if scenario_agreement >= 0 else 'under'
                context_notes.append(
                    f"Scenario splits: {scenario_matches} matches, "
                    f"lean {lean} {scenario_agreement:+.2f}")
                if pack_fresh:      # stale conditioning never nudges tiers
                    confidence_tier = _apply_scenario_nudge(
                        confidence_tier, scenario_agreement, scenario_matches)

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
            'context_notes': context_notes,
            'std_dev': round(std_dev, 2),
            'z_score': proj.get('z_score', 0),
            'games_played': games_played,
            'confidence': projection_confidence,
            'projection_source': projection_source,
            'scenario_agreement': scenario_agreement,
            'scenario_matches': scenario_matches,
            'breakdown': proj.get('breakdown', {}),
            'game_id': game_id,
            'win_probability': win_probability,
            'pick_quality_recommendation': pick_quality_recommendation,
        }

    def _model_prob_over(
        self,
        projection: float,
        line: float,
        std_dev: float,
        player_name: str = '',
        prop_type: str = '',
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
        game_date: Optional[_date] = None,
        game_total_line: float = 0.0,
    ) -> float:
        """Estimate probability of the player exceeding the line."""
        prob, _ = self._model_prob_over_details(
            projection, line, std_dev,
            player_name=player_name, prop_type=prop_type,
            opponent_name=opponent_name, team_name=team_name,
            is_home=is_home, game_date=game_date, game_total_line=game_total_line,
        )
        return prob

    def _model_prob_over_details(
        self,
        projection: float,
        line: float,
        std_dev: float,
        player_name: str = '',
        prop_type: str = '',
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
        game_date: Optional[_date] = None,
        game_total_line: float = 0.0,
    ) -> tuple:
        """Return ``(prob_over, dist_point_or_None)`` for the line.

        When USE_DISTRIBUTIONAL_MODEL=true and a trained distributional
        model is available for (player_name, prop_type), P(over) comes from
        the calibrated model CDF (quantile heads for points/rebounds/
        assists/PRA, Poisson CDF for threes/steals/blocks) and the second
        element is the distribution's own point estimate. Otherwise (flag
        off, or no model/features available) falls back to the legacy
        Normal(projection, std_dev) synthetic CDF — byte-identical to
        pre-Plan-C behavior — and the second element is None.
        """
        if self._use_distributional_model() and player_name and prop_type:
            try:
                stat_type, features = self._build_dist_features(
                    player_name, prop_type, opponent_name, team_name, is_home, game_date,
                    game_total_line,
                )
                if features:
                    from app.services.distributional_predictor import (
                        predict_prob_over_details,
                    )
                    details = predict_prob_over_details(stat_type, features, line)
                    if details is not None:
                        return float(details['prob_over']), float(details['point'])
            except Exception as exc:
                logger.warning(
                    "Distributional P(over) failed for %s/%s; falling back to Gaussian: %s",
                    player_name, prop_type, exc,
                )

        if std_dev <= 0:
            return (0.65 if projection > line else 0.35), None

        try:
            from scipy.stats import norm
            return float(1.0 - norm.cdf(line, loc=projection, scale=std_dev)), None
        except ImportError:
            # Fallback: approximate normal CDF using the error function
            z = (line - projection) / std_dev
            return 0.5 * (1.0 + math.erf(-z / math.sqrt(2))), None

    def _use_distributional_model(self) -> bool:
        return os.getenv('USE_DISTRIBUTIONAL_MODEL', 'false').lower() == 'true'

    def _use_scenario_signal(self) -> bool:
        return os.getenv('USE_SCENARIO_SIGNAL', 'false').lower() == 'true'

    def _scenario_signal(self, player_name, prop_type, line, opponent_name,
                         team_name, is_home, game_date, game_total_line,
                         spread, favored_side):
        """Return ``(agreement, matches, pack_fresh)`` or None (no signal)."""
        stat = PROP_TO_SPLIT_STAT.get(prop_type)
        if stat is None:
            return None
        from app.services.player_crosswalk import resolve_espn_id
        espn_id = resolve_espn_id(player_name)
        if espn_id is None:
            return None
        from app.services.live_context import build_live_context, get_live_pack
        from app.services.scenario_engine import (
            agreement_score, load_agreement_splits,
        )
        cache = self._scenario_scan_cache
        if cache is None:
            context, fresh = build_live_context(
                espn_id, team_abbr=team_name, opponent_abbr=opponent_name,
                is_home=is_home, game_date=game_date,
                total=game_total_line or None, spread=spread,
                favored_side=favored_side)
            score, matches = agreement_score(espn_id, stat, line, context)
        else:
            if 'pack' not in cache:
                cache['pack'] = get_live_pack()
            ctx_key = (espn_id, team_name, opponent_name, is_home, game_date,
                       game_total_line or None, spread, favored_side)
            if ctx_key not in cache['context']:
                cache['context'][ctx_key] = build_live_context(
                    espn_id, team_abbr=team_name,
                    opponent_abbr=opponent_name, is_home=is_home,
                    game_date=game_date, total=game_total_line or None,
                    spread=spread, favored_side=favored_side,
                    pack=cache['pack'])
            context, fresh = cache['context'][ctx_key]
            splits_key = (espn_id, stat)
            if splits_key not in cache['splits']:
                cache['splits'][splits_key] = load_agreement_splits(
                    espn_id, stat)
            score, matches = agreement_score(
                espn_id, stat, line, context,
                splits=cache['splits'][splits_key])
        if matches == 0:
            return None
        return score, matches, fresh

    def _build_dist_features(
        self,
        player_name: str,
        prop_type: str,
        opponent_name: str,
        team_name: str,
        is_home: bool,
        game_date: Optional[_date],
        game_total_line: float = 0.0,
    ):
        """Build the 30-key ML feature dict for the distributional predictor.

        Reuses ProjectionEngine's already-cached (player_id, logs, summary)
        from the project_stat() call score_prop() just made — no extra DB
        round trip. Returns (prop_type, features) or (None, None) when
        unavailable (caller falls back to the synthetic Gaussian).
        """
        from app.services.distributional_model import DIST_STAT_KEY_MAP, wrap_pra_logs
        from app.services.ml_model import _build_defense_lookup

        player_cache_key = str(player_name).strip().lower()
        state = self.engine._player_state_cache.get(player_cache_key)
        if not state:
            return None, None
        _, logs, _ = state
        if len(logs) < 10:
            return None, None

        stat_key = DIST_STAT_KEY_MAP.get(prop_type)
        use_logs = logs
        if stat_key:
            if stat_key == 'pra':
                use_logs = wrap_pra_logs(logs)
        else:
            stat_key = PROP_STAT_KEY.get(prop_type)
            if not stat_key:
                return None, None

        defense_cache_key = '__dist_defense_lookup__'
        defense_lookup = self.engine._context_cache.get(defense_cache_key)
        if defense_lookup is None:
            try:
                defense_lookup = _build_defense_lookup()
            except Exception:
                defense_lookup = {}
            self.engine._context_cache[defense_cache_key] = defense_lookup

        current_matchup = ''
        if team_name and opponent_name:
            sep = ' vs. ' if is_home else ' @ '
            current_matchup = f"{team_name}{sep}{opponent_name}"

        features = self.engine._build_ml_features(
            use_logs, stat_key, is_home,
            current_matchup=current_matchup,
            game_total_line=game_total_line,
            defense_lookup=defense_lookup,
            game_date=game_date,
        )
        return prop_type, features

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
            'book_prob_over': devig_probs(over_odds, under_odds)[0],
            'book_prob_under': devig_probs(over_odds, under_odds)[1],
            'context_notes': [],
            'std_dev': 0,
            'z_score': 0,
            'games_played': 0,
            'confidence': 'low',
            'projection_source': 'heuristic',
            'scenario_agreement': None,
            'scenario_matches': None,
            'breakdown': {},
            'game_id': game_id,
        }

    def score_all_todays_props(self, games: list = None) -> list:
        """Score all available props across today's NBA games.

        Returns a list of score dicts sorted by absolute edge descending.

        Results are cached for _SCORE_CACHE_TTL seconds (keyed by ET date) when
        called without an explicit games list, so repeated dashboard/analysis
        page loads skip the expensive API + DB computation within the TTL window.
        Pass games explicitly (e.g. in tests or the scheduler) to bypass the cache.
        """
        use_cache = games is None
        _t0 = _time.perf_counter()

        if use_cache:
            cache_date = et_date_str()
            cached = _SCORE_CACHE.get(cache_date)
            if cached and _time.monotonic() < cached["expires_at"]:
                logger.info("PERF score_all_todays_props: cache_hit scores=%d elapsed=0.00s", len(cached["scores"]))
                return list(cached["scores"])

            from app.services.nba_service import get_todays_games
            _t_games = _time.perf_counter()
            games = get_todays_games()
            logger.info("PERF get_todays_games: games=%d elapsed=%.2fs", len(games), _time.perf_counter() - _t_games)

        from app.services.nba_service import fetch_player_props_for_event

        all_scores = []

        # Prefetch props for all games in parallel to avoid sequential HTTP stalls.
        games_with_events = [(g, g.get('odds_event_id', '')) for g in games if g.get('odds_event_id', '')]

        def _fetch(game_event):
            # Workers do HTTP only — no ORM/DB access inside threads.
            game, event_id = game_event
            _t = _time.perf_counter()
            try:
                props = fetch_player_props_for_event(event_id)
            except Exception as exc:
                logger.error("Failed to fetch props for event %s: %s", event_id, exc)
                props = {}
            elapsed = _time.perf_counter() - _t
            prop_count = sum(len(v) for v in props.values())
            logger.info("PERF props_fetch event=%s props=%d elapsed=%.2fs", event_id[:8], prop_count, elapsed)
            return game, props

        raw_results: list[tuple] = []
        all_player_names: set[str] = set()
        _t_props = _time.perf_counter()
        with ThreadPoolExecutor(max_workers=min(8, len(games_with_events) or 1)) as pool:
            futures = {pool.submit(_fetch, ge): ge for ge in games_with_events}
            for future in as_completed(futures):
                game, props = future.result()
                raw_results.append((game, props))

        # Snapshot fallback in main thread — safe DB access, no thread-context issues.
        # For any game that returned empty props (e.g. 429 rate limit), try the cached
        # GameSnapshot.props_json written by the scheduler's prefetch job.
        from app.models import GameSnapshot
        today = datetime.now(ET).date()
        empty_espn_ids = [g.get('espn_id', '') for g, p in raw_results if not p and g.get('espn_id')]
        if empty_espn_ids:
            snaps = {
                s.espn_id: s
                for s in GameSnapshot.query.filter(
                    GameSnapshot.espn_id.in_(empty_espn_ids),
                    GameSnapshot.game_date == today,
                ).all()
            }
            filled = 0
            for i, (game, props) in enumerate(raw_results):
                if props:
                    continue
                espn_id = game.get('espn_id', '')
                snap = snaps.get(espn_id)
                if snap and snap.props_json:
                    try:
                        raw_results[i] = (game, json.loads(snap.props_json))
                        filled += 1
                        logger.info("PERF props_fetch espn_id=%s using cached snapshot fallback", espn_id[:8])
                    except (ValueError, TypeError):
                        pass
            if filled:
                logger.info("Snapshot fallback filled props for %d game(s)", filled)

        game_props_payloads = []
        for game, props in raw_results:
            if not props:
                continue
            game_props_payloads.append((game, props))
            for market_props in props.values():
                for prop in market_props:
                    player = prop.get('player', '')
                    if player:
                        all_player_names.add(player)
        logger.info("PERF props_fetch_parallel: games=%d wall_elapsed=%.2fs", len(game_props_payloads), _time.perf_counter() - _t_props)

        _t_team = _time.perf_counter()
        player_team_map = self._build_player_team_map(all_player_names)
        logger.info("PERF player_team_map: players=%d elapsed=%.2fs", len(player_team_map), _time.perf_counter() - _t_team)

        _t_score = _time.perf_counter()
        # Fresh per-scan scenario cache — see __init__. Rebuilt every scan so
        # a refreshed pack/splits set is picked up on the next scan.
        self._scenario_scan_cache = {'context': {}, 'splits': {}}
        try:
            for game, props in game_props_payloads:
                espn_id = game.get('espn_id', '')
                home_team = game.get('home', {}).get('name', '')
                away_team = game.get('away', {}).get('name', '')
                home_abbr = (game.get('home', {}).get('abbr') or '').upper()
                away_abbr = (game.get('away', {}).get('abbr') or '').upper()

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

                        team_name, opponent_name, is_home = self._resolve_game_context_for_player(
                            player_name=player,
                            home_team=home_team,
                            away_team=away_team,
                            home_abbr=home_abbr,
                            away_abbr=away_abbr,
                            player_team_map=player_team_map,
                        )

                        _start_time_str = game.get('start_time', '')[:10]
                        try:
                            _game_date: Optional[_date] = (
                                _date.fromisoformat(_start_time_str) if _start_time_str else None
                            )
                        except ValueError:
                            _game_date = None

                        score = self.score_prop(
                            player_name=player,
                            prop_type=market_key,
                            line=line,
                            over_odds=over_odds,
                            under_odds=under_odds,
                            opponent_name=opponent_name,
                            team_name=team_name,
                            is_home=is_home,
                            game_id=espn_id,
                            game_date=_game_date,
                            game_total_line=float(game.get('over_under_line') or 0.0),
                            spread=game.get('spread'),
                            favored_side=game.get('favored_side'),
                        )

                        # Add game context to score
                        score['home_team'] = home_team
                        score['away_team'] = away_team
                        score['match_date'] = _start_time_str

                        all_scores.append(score)
        finally:
            self._scenario_scan_cache = None

        # Sort by absolute edge descending
        all_scores.sort(key=lambda s: abs(s.get('edge', 0)), reverse=True)
        logger.info("PERF scoring_loop: scored=%d elapsed=%.2fs", len(all_scores), _time.perf_counter() - _t_score)

        # Populate module-level cache for subsequent requests within the TTL window.
        if use_cache:
            cache_date = et_date_str()
            _SCORE_CACHE.clear()  # drop any prior-date entry
            _SCORE_CACHE[cache_date] = {
                "scores": all_scores,
                "expires_at": _time.monotonic() + _SCORE_CACHE_TTL,
            }
        logger.info("PERF score_all_todays_props: total_elapsed=%.2fs scores=%d", _time.perf_counter() - _t0, len(all_scores))

        return all_scores

    def get_top_plays(self, min_edge: float = TIER_SLIGHT, max_plays: int = 20) -> list:
        """Return the top value plays from today's props.

        Filters by minimum edge and excludes no_edge/low confidence plays.
        """
        all_scores = self.score_all_todays_props()
        top = self.filter_plays(all_scores, min_edge=min_edge)
        return top[:max_plays]

    def recommend_best_parlay(
        self,
        scores: Optional[list] = None,
        min_edge: float = TIER_MODERATE,
        min_odds: int = 100,
        max_odds: int = 200,
        min_legs: int = 2,
        max_legs: int = 3,
    ) -> Optional[dict]:
        """Return best 2-3 leg high-confidence parlay within target odds range."""
        if scores is None:
            scores = self.score_all_todays_props()

        candidates = []
        for play in self.filter_plays(scores, min_edge=min_edge):
            if play.get('confidence_tier') != 'strong':
                continue
            odds = play.get('recommended_odds')
            if odds is None:
                continue
            candidates.append(play)

        # Keep search space small and deterministic.
        candidates = sorted(
            candidates,
            key=lambda p: (p.get('edge', 0), p.get('games_played', 0)),
            reverse=True,
        )[:12]
        if len(candidates) < min_legs:
            return None

        best_combo = None
        best_score = None
        for legs_count in range(min_legs, max_legs + 1):
            for combo in combinations(candidates, legs_count):
                unique_keys = {(c.get('player'), c.get('prop_type'), c.get('game_id')) for c in combo}
                if len(unique_keys) != len(combo):
                    continue

                dec_prod = 1.0
                for leg in combo:
                    dec_prod *= decimal_odds(int(leg.get('recommended_odds')))
                parlay_american = american_from_decimal(dec_prod)
                if parlay_american < min_odds or parlay_american > max_odds:
                    continue

                combo_edge = sum(float(c.get('edge', 0) or 0) for c in combo)
                ranking_score = combo_edge + 0.001 * sum(int(c.get('games_played', 0) or 0) for c in combo)

                if best_score is None or ranking_score > best_score:
                    best_score = ranking_score
                    best_combo = {
                        'combined_odds': parlay_american,
                        'legs': [{
                            'player': c.get('player'),
                            'prop_type': c.get('prop_type'),
                            'line': c.get('line'),
                            'side': c.get('recommended_side'),
                            'odds': c.get('recommended_odds'),
                            'edge': c.get('edge'),
                            'game_id': c.get('game_id'),
                            'home_team': c.get('home_team'),
                            'away_team': c.get('away_team'),
                            'match_date': c.get('match_date'),
                        } for c in combo],
                        'total_edge': round(combo_edge, 4),
                        'confidence': 'high',
                    }

        return best_combo

    @staticmethod
    def filter_plays(scores: list, min_edge: float = TIER_SLIGHT) -> list:
        """Filter scores down to actionable value plays."""
        return [
            s for s in scores
            if abs(s.get('edge', 0)) >= min_edge
            and s.get('confidence_tier') != 'no_edge'
            and s.get('games_played', 0) >= 10
        ]


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
