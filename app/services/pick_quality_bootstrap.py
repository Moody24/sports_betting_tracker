"""Synthetic resolved examples for the pick-quality training bootstrap."""

import json
import random
from datetime import datetime, timezone, time as dt_time

from app.enums import BetSource, Outcome
from app.services.auto_pick_service import _ensure_autopicks_user
from app.services.feature_engine import build_pick_context_features
from app.services.stats_service import get_player_stats_summary


def _bootstrap_tier(edge: float) -> str:
    if edge >= 0.15:
        return 'strong'
    if edge >= 0.08:
        return 'moderate'
    return 'slight'


def _bootstrap_opponent(log) -> str:
    matchup = log.matchup or ''
    if ' vs. ' in matchup:
        return matchup.split(' vs. ', 1)[1].strip()
    if ' @ ' in matchup:
        return matchup.split(' @ ', 1)[1].strip()
    return ''


def _bootstrap_market_values(log, rng, stat_map) -> dict | None:
    prop_type, stat_key, spread = rng.choice(stat_map)
    actual = float(getattr(log, stat_key, 0) or 0)
    if actual < 0:
        return None
    line = max(0.5, round(actual + rng.uniform(-spread, spread)) + 0.5)
    bet_type = 'over' if rng.random() >= 0.5 else 'under'
    is_win = actual > line if bet_type == 'over' else actual < line
    return {
        'prop_type': prop_type,
        'stat_key': stat_key,
        'actual': actual,
        'line': line,
        'bet_type': bet_type,
        'outcome': Outcome.WIN.value if is_win else Outcome.LOSE.value,
        'odds': (-110 if rng.random() >= 0.3
                 else int(rng.choice([100, 105, 110, 115, 120]))),
    }


def _create_bootstrap_example(log, user_id: int, rng, stat_map) -> bool:
    from app import db
    from app.models import Bet, PickContext

    if not log.game_date or log.game_date >= datetime.now(timezone.utc).date():
        return False
    market = _bootstrap_market_values(log, rng, stat_map)
    if market is None:
        return False
    summary = get_player_stats_summary(str(log.player_id), logs=None)
    season_avg = float(
        summary.get('season', {}).get(
            market['stat_key'], market['actual'],
        ) or market['actual']
    )
    projected_stat = season_avg + rng.uniform(-1.5, 1.5)
    projected_edge = (
        (projected_stat - market['line']) / max(market['line'], 1.0)
    )
    if market['bet_type'] == 'under':
        projected_edge = (
            (market['line'] - projected_stat) / max(market['line'], 1.0)
        )
    tier = _bootstrap_tier(abs(projected_edge))
    context = build_pick_context_features(
        player_name=log.player_name,
        player_id=str(log.player_id),
        prop_type=market['prop_type'],
        prop_line=float(market['line']),
        american_odds=int(market['odds']),
        projected_stat=float(round(projected_stat, 2)),
        projected_edge=float(round(projected_edge, 4)),
        confidence_tier=tier,
        opponent_name=_bootstrap_opponent(log),
        team_name=str(log.team_abbr or ''),
        is_home=(log.home_away or '').lower() == 'home',
    )
    bet_obj = Bet(
        user_id=user_id,
        team_a=str(log.team_abbr or 'TEAM'),
        team_b='OPP',
        match_date=datetime.combine(log.game_date, dt_time.min),
        bet_amount=10.0,
        outcome=market['outcome'],
        american_odds=int(market['odds']),
        is_parlay=False,
        source=BetSource.AUTO_GENERATED.value,
        bet_type=market['bet_type'],
        over_under_line=None,
        external_game_id=None,
        player_name=log.player_name,
        prop_type=market['prop_type'],
        prop_line=float(market['line']),
        actual_total=float(market['actual']),
        notes='AUTO_BOOTSTRAP_HIDDEN:model2',
    )
    db.session.add(bet_obj)
    db.session.flush()
    db.session.add(PickContext(
        bet_id=bet_obj.id,
        context_json=json.dumps(context),
        projected_stat=float(round(projected_stat, 2)),
        projected_edge=float(round(projected_edge, 4)),
        confidence_tier=tier,
    ))
    return True


def bootstrap_pick_quality_examples(app, target_resolved: int = 220, max_logs: int = 10000) -> dict:
    from app import db
    from app.models import Bet, PickContext, PlayerGameLog, User

    with app.app_context():
        system_user = _ensure_autopicks_user(db, User)

        existing_resolved = (
            db.session.query(Bet)
            .join(PickContext, PickContext.bet_id == Bet.id)
            .filter(Bet.user_id == system_user.id)
            .filter(Bet.source == BetSource.AUTO_GENERATED.value)
            .filter(Bet.notes.like('AUTO_BOOTSTRAP_HIDDEN%'))
            .filter(Bet.outcome.in_([Outcome.WIN.value, Outcome.LOSE.value]))
            .count()
        )
        if existing_resolved >= target_resolved:
            return {
                'created': 0,
                'existing_resolved': existing_resolved,
                'target_resolved': target_resolved,
                'message': 'already at target',
            }

        needed = target_resolved - existing_resolved
        rng = random.Random(42)
        created = 0
        scan_count = 0

        stat_map = [
            ('player_points', 'pts', 4.5),
            ('player_rebounds', 'reb', 2.5),
            ('player_assists', 'ast', 2.5),
            ('player_threes', 'fg3m', 1.5),
        ]

        logs = (
            PlayerGameLog.query
            .filter(PlayerGameLog.game_date.isnot(None))
            .order_by(PlayerGameLog.game_date.desc(), PlayerGameLog.id.desc())
            .limit(max_logs)
            .all()
        )

        for log in logs:
            if created >= needed:
                break
            scan_count += 1
            was_created = _create_bootstrap_example(log, system_user.id, rng, stat_map)
            created += int(was_created)

            if was_created and created % 100 == 0:
                db.session.commit()

        db.session.commit()
        return {
            'created': created,
            'created_context': created,
            'existing_resolved': existing_resolved,
            'target_resolved': target_resolved,
            'logs_scanned': scan_count,
        }
