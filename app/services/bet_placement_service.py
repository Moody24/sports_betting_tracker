"""Validation and persistence for multi-leg bet placement entry points."""

import uuid
from datetime import datetime

from app import db
from app.enums import BetSource, BetType, Outcome
from app.models import Bet
from app.services.bet_context_service import create_pick_context_for_bet
from app.services.projection_engine import ProjectionEngine
from app.services.value_detector import ValueDetector
from app.utils.time_helpers import ET


class BetPlacementError(ValueError):
    """Raised with a user-safe message when placement data is invalid."""


def parse_stake(value) -> float:
    try:
        stake = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise BetPlacementError('Stake must be a number') from exc
    if stake <= 0:
        raise BetPlacementError('Stake must be greater than zero')
    return stake


def parse_optional_units(value) -> float | None:
    if value is None:
        return None
    try:
        units = float(value)
    except (TypeError, ValueError):
        return None
    return units if units > 0 else None


def parse_bonus_multiplier(value) -> float:
    try:
        multiplier = float(value or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return max(multiplier, 1.0)


def parse_round_robin_size(payload) -> int | None:
    if not isinstance(payload, dict):
        return None
    try:
        return int(payload.get('size') or 0) or None
    except (TypeError, ValueError):
        return None


def normalize_single_bet_lines(
    bet_type: str,
    prop_type: str | None,
    prop_line: float | None,
    total_line: float | None,
) -> tuple[float | None, float | None]:
    """Return ``(prop_line, total_line)`` for a single bet or raise safely."""
    if bet_type not in (BetType.OVER.value, BetType.UNDER.value):
        return prop_line, total_line
    if prop_type:
        if prop_line is None:
            raise BetPlacementError('A prop line is required for player props.')
        return prop_line, None
    normalized_total = total_line if total_line is not None else prop_line
    if normalized_total is None:
        raise BetPlacementError('A line is required for totals (Over/Under).')
    return None, normalized_total


def _match_datetime(value) -> datetime:
    try:
        return datetime.strptime(str(value or ''), '%Y-%m-%d')
    except ValueError:
        return datetime.now(ET)


def _validated_leg(leg, index: int) -> None:
    if not isinstance(leg, dict):
        raise BetPlacementError(f'Leg {index}: must be an object')
    if not leg.get('team_a') or not leg.get('team_b'):
        raise BetPlacementError(f'Leg {index}: team_a and team_b are required')


def _manual_prop_line(leg: dict, index: int) -> float | None:
    if not leg.get('prop_line'):
        return None
    try:
        line = float(leg['prop_line'])
    except (TypeError, ValueError) as exc:
        raise BetPlacementError(f'Leg {index}: prop_line must be a number') from exc
    if not -50 < line < 100:
        raise BetPlacementError(f'Leg {index}: prop_line out of range (-50, 100)')
    return line


def _manual_odds(leg: dict, index: int) -> int | None:
    value = leg.get('american_odds', leg.get('odds'))
    if value in (None, ''):
        return None
    try:
        odds = int(value)
    except (TypeError, ValueError) as exc:
        raise BetPlacementError(
            f'Leg {index}: american_odds must be an integer'
        ) from exc
    if not -5000 <= odds <= 5000:
        raise BetPlacementError(
            f'Leg {index}: american_odds out of range (-5000, 5000)'
        )
    return odds or None


def _manual_total_line(leg: dict, bet_type: str, player_name: str | None):
    if bet_type not in (BetType.OVER.value, BetType.UNDER.value) or player_name:
        return None
    try:
        return float(leg['over_under_line']) if leg.get('over_under_line') else None
    except (TypeError, ValueError):
        return None


def _manual_bet(
    *,
    user_id: int,
    leg: dict,
    index: int,
    stake: float,
    units: float | None,
    outcome: str,
    parlay_id: str,
) -> Bet:
    _validated_leg(leg, index)
    bet_type = leg.get('bet_type', BetType.MONEYLINE.value)
    player_name = str(leg.get('player_name') or '')[:100] or None
    prop_type = str(leg.get('prop_type') or '')[:40] or None
    return Bet(
        user_id=user_id,
        team_a=str(leg['team_a'])[:80],
        team_b=str(leg['team_b'])[:80],
        match_date=_match_datetime(leg.get('match_date')),
        bet_amount=stake,
        units=units,
        outcome=outcome,
        american_odds=_manual_odds(leg, index),
        bet_type=bet_type,
        over_under_line=_manual_total_line(leg, bet_type, player_name),
        prop_line=_manual_prop_line(leg, index),
        player_name=player_name,
        prop_type=prop_type,
        picked_team=str(leg.get('picked_team') or '')[:80] or None,
        external_game_id=leg.get('game_id') or None,
        is_parlay=True,
        parlay_id=parlay_id,
        source=BetSource.MANUAL.value,
    )


def build_manual_parlay_bets(
    *,
    user_id: int,
    legs: list,
    stake: float,
    units: float | None,
    outcome: str = Outcome.PENDING.value,
) -> list[Bet]:
    """Validate manual-parlay legs and return unsaved Bet rows."""
    parlay_id = Bet.generate_parlay_id()
    created = []
    errors = []
    for index, leg in enumerate(legs, start=1):
        try:
            created.append(_manual_bet(
                user_id=user_id,
                leg=leg,
                index=index,
                stake=stake,
                units=units,
                outcome=outcome,
                parlay_id=parlay_id,
            ))
        except BetPlacementError as exc:
            errors.append(str(exc))
    if errors:
        raise BetPlacementError('; '.join(errors))
    return created


def _lenient_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _lenient_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nba_bet(
    *,
    user_id: int,
    leg: dict,
    index: int,
    stake: float,
    units: float | None,
    is_parlay: bool,
    parlay_id: str | None,
    bonus_multiplier: float,
    round_robin_size: int | None,
) -> Bet:
    _validated_leg(leg, index)
    prop_line = _lenient_float(leg.get('prop_line'))
    player_name = str(leg.get('player_name') or '')[:100] or None
    prop_type = str(leg.get('prop_type') or '')[:40] or None
    is_player_prop = bool(player_name and prop_type and prop_line is not None)
    total_line = _lenient_float(leg.get('over_under_line'))
    if not is_player_prop and total_line is None:
        total_line = prop_line
    bet_type = leg.get('bet_type', BetType.OVER.value)
    picked_team = str(leg.get('picked_team') or '')[:80] or None
    return Bet(
        user_id=user_id,
        team_a=str(leg['team_a'])[:80],
        team_b=str(leg['team_b'])[:80],
        match_date=_match_datetime(leg.get('match_date')),
        bet_amount=stake,
        units=units,
        outcome=Outcome.PENDING.value,
        bet_type=bet_type,
        over_under_line=None if is_player_prop else total_line,
        picked_team=picked_team if bet_type == BetType.MONEYLINE.value else None,
        american_odds=_lenient_int(leg.get('american_odds')),
        external_game_id=leg.get('game_id') or None,
        player_name=player_name,
        prop_type=prop_type,
        prop_line=prop_line if is_player_prop else None,
        is_parlay=is_parlay,
        parlay_id=parlay_id,
        source=BetSource.NBA_PROPS.value,
        bonus_multiplier=bonus_multiplier,
        round_robin_size=round_robin_size,
    )


def build_nba_bets(
    *,
    user_id: int,
    legs: list,
    stake: float,
    units: float | None,
    is_parlay: bool,
    bonus_multiplier: float,
    round_robin_size: int | None,
) -> list[Bet]:
    """Validate NBA slip legs and return unsaved Bet rows."""
    parlay_id = Bet.generate_parlay_id() if is_parlay else None
    created = []
    errors = []
    for index, leg in enumerate(legs, start=1):
        try:
            created.append(_nba_bet(
                user_id=user_id,
                leg=leg,
                index=index,
                stake=stake,
                units=units,
                is_parlay=is_parlay,
                parlay_id=parlay_id,
                bonus_multiplier=bonus_multiplier,
                round_robin_size=round_robin_size,
            ))
        except BetPlacementError as exc:
            errors.append(str(exc))
    if errors:
        raise BetPlacementError('; '.join(errors))
    return created


def persist_new_bets(bets: list[Bet], round_robin_size: int | None = None) -> None:
    """Save new bets and their learning context in one transaction."""
    if not bets:
        return
    leg_count = len(bets)
    if bets[0].is_parlay:
        for bet in bets:
            bet.parlay_leg_count = leg_count
    if round_robin_size and leg_count >= round_robin_size:
        group_id = str(uuid.uuid4())[:40]
        for bet in bets:
            bet.parlay_group_id = group_id

    db.session.add_all(bets)
    db.session.flush()
    detector = ValueDetector(ProjectionEngine())
    for bet in bets:
        create_pick_context_for_bet(
            bet_obj=bet,
            detector=detector,
            selected_odds=bet.american_odds,
        )
    db.session.commit()
