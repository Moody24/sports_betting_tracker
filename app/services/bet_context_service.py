"""Pick-context persistence shared by every bet placement entry point."""

import json

from app import db
from app.enums import BetType
from app.models import Bet, PickContext
from app.services.feature_engine import build_pick_context_features
from app.services.stats_service import find_player_id
from app.services.value_detector import ValueDetector


def create_pick_context_for_bet(
    bet_obj: Bet,
    detector: ValueDetector,
    selected_odds: int | None = None,
    team_name: str = '',
    opponent_name: str = '',
    is_home: bool = True,
) -> None:
    """Persist the model-training context for a newly created player prop."""
    if not bet_obj.is_player_prop or bet_obj.prop_line is None:
        return

    player_id = find_player_id(bet_obj.player_name or '')
    if not player_id:
        return

    market_odds = int(selected_odds) if selected_odds is not None else -110
    score = detector.score_prop(
        player_name=bet_obj.player_name or '',
        prop_type=bet_obj.prop_type or '',
        line=float(bet_obj.prop_line),
        over_odds=market_odds,
        under_odds=market_odds,
        opponent_name=opponent_name,
        team_name=team_name,
        is_home=is_home,
        game_id=bet_obj.external_game_id or '',
    )

    projected_edge = score.get('edge', 0.0)
    if bet_obj.bet_type == BetType.OVER.value:
        projected_edge = score.get('edge_over', projected_edge)
    elif bet_obj.bet_type == BetType.UNDER.value:
        projected_edge = score.get('edge_under', projected_edge)

    context = build_pick_context_features(
        player_name=bet_obj.player_name or '',
        player_id=str(player_id),
        prop_type=bet_obj.prop_type or '',
        prop_line=float(bet_obj.prop_line),
        american_odds=market_odds,
        projected_stat=float(score.get('projection', 0.0) or 0.0),
        projected_edge=float(projected_edge or 0.0),
        confidence_tier=score.get('confidence_tier', 'no_edge'),
        opponent_name=opponent_name,
        team_name=team_name,
        is_home=is_home,
    )

    db.session.add(PickContext(
        bet_id=bet_obj.id,
        context_json=json.dumps(context),
        projected_stat=score.get('projection'),
        projected_edge=projected_edge,
        confidence_tier=score.get('confidence_tier'),
    ))
