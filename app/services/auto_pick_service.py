"""Selection and persistence of daily NBA auto picks."""

import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta, time as dt_time, timezone

from app import db
from app.enums import BetSource, Outcome
from app.models import Bet, PickContext, User
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

AUTO_PICK_MAX_TOTAL = int(os.getenv('AUTO_PICK_MAX_TOTAL', '50'))
AUTO_PICK_MIN_EDGE_STRAIGHT = float(os.getenv('AUTO_PICK_MIN_EDGE_STRAIGHT', '0.15'))
AUTO_PICK_MIN_EDGE_2LEG = float(os.getenv('AUTO_PICK_MIN_EDGE_2LEG', '0.15'))
AUTO_PICK_MIN_EDGE_3LEG = float(os.getenv('AUTO_PICK_MIN_EDGE_3LEG', '0.15'))
AUTO_PICK_MIN_GAMES = int(os.getenv('AUTO_PICK_MIN_GAMES', '15'))
AUTO_PICK_CONFIDENCE_TIER = os.getenv('AUTO_PICK_CONFIDENCE_TIER', 'strong')
AUTO_PAPER_ENABLED = os.getenv('AUTO_PAPER_ENABLED', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
AUTO_PAPER_MAX_PER_COHORT = int(os.getenv('AUTO_PAPER_MAX_PER_COHORT', '24'))
AUTO_PAPER_MIN_GAMES = int(os.getenv('AUTO_PAPER_MIN_GAMES', '10'))
AUTO_PAPER_COHORTS = (
    {'name': 'mid_edge', 'min_edge': 0.08, 'max_edge': 0.15, 'min_tier': 'moderate'},
    {'name': 'low_edge', 'min_edge': 0.03, 'max_edge': 0.08, 'min_tier': 'slight'},
)


def auto_pick_data_issues(score: dict, today: date) -> list[str]:
    from app.services.context_service import player_availability
    from app.services.espn_mapping import season_for_date
    from app.services.matchup_service import get_team_defense

    issues = []
    if score.get('history_source') != 'espn_history':
        issues.append('player identity or history unverified')
    observed = score.get('history_observed_through')
    try:
        history_date = date.fromisoformat(str(observed))
    except ValueError:
        history_date = None
    if (history_date is None or (today - history_date).days < 0
            or (today - history_date).days > 30):
        issues.append('player game history stale')

    if not score.get('player_team') or not score.get('opponent_team'):
        issues.append('player team unresolved')

    if player_availability(str(score.get('player') or '')) != 'available':
        issues.append('injury availability unverified')

    opponent = str(score.get('opponent_team') or '')
    defense = get_team_defense(opponent) if opponent else {}
    observed_defense = defense.get('observed_through')
    season_start = date(int(season_for_date(today)[:4]), 10, 1)
    if (not observed_defense or defense.get('source') not in
            ('espn_boxscore_current', 'nba_api', 'sportsdataverse_team_box')
            or observed_defense < season_start
            or not 0 <= (today - observed_defense).days <= 21
            or (defense.get('sample_games') or 0) < 5):
        issues.append('opponent defense unverified')

    try:
        quote_time = datetime.fromisoformat(str(score.get('quote_observed_at')))
        if quote_time.tzinfo is None:
            raise ValueError('naive quote timestamp')
        quote_age = datetime.now(timezone.utc) - quote_time.astimezone(timezone.utc)
    except ValueError:
        quote_age = timedelta.max
    if (score.get('quote_source') != 'odds_api'
            or not score.get('odds_event_id') or quote_age < timedelta(0)
            or quote_age > timedelta(minutes=10)):
        issues.append('offered prop quote unverified')
    return issues


def _build_auto_pick_context(bet_obj, score: dict) -> dict:
    from app.services.feature_engine import build_pick_context_features
    from app.services.player_identity_service import get_verified_history
    from app.utils.time_helpers import et_today

    history = get_verified_history(bet_obj.player_name or '', as_of=et_today(), limit=1)
    if not history:
        return {}
    player_id = history[0].player_id

    selected_odds = int(score.get('recommended_odds') or -110)
    projected_edge = score.get('edge', 0.0)
    if bet_obj.bet_type == 'over':
        projected_edge = score.get('edge_over', projected_edge)
    elif bet_obj.bet_type == 'under':
        projected_edge = score.get('edge_under', projected_edge)

    home_team = str(score.get('home_team') or '')
    away_team = str(score.get('away_team') or '')
    player_team = str(score.get('player_team') or '')
    is_home = bool(score.get('is_home', True))
    opponent_name = away_team if is_home else home_team

    return build_pick_context_features(
        player_name=bet_obj.player_name or '',
        player_id=str(player_id),
        prop_type=bet_obj.prop_type or '',
        prop_line=float(bet_obj.prop_line or 0.0),
        american_odds=selected_odds,
        projected_stat=float(score.get('projection', 0.0) or 0.0),
        projected_edge=float(projected_edge or 0.0),
        confidence_tier=score.get('confidence_tier', 'no_edge'),
        opponent_name=opponent_name,
        team_name=player_team,
        is_home=is_home,
    )


def _ensure_autopicks_user(db, User):
    system_user = User.query.filter_by(username='__autopicks__').first()
    if system_user is None:
        system_user = User(username='__autopicks__', email='autopicks@local.invalid')
        system_user.set_password(secrets.token_hex(32))
        db.session.add(system_user)
        db.session.flush()
    return system_user


def _build_candidates(scores: list, min_games: int, *,
                      require_verified: bool = False, today: date | None = None) -> list:
    seen_keys: set = set()
    candidates: list = []
    for score in sorted(scores, key=lambda item: item.get('edge', 0), reverse=True):
        if (score.get('games_played') or 0) < min_games:
            continue
        if require_verified:
            issues = auto_pick_data_issues(score, today or datetime.now(ET).date())
            if issues:
                logger.info('Auto pick abstained for %s: %s',
                            score.get('player'), ', '.join(issues))
                continue
        key = (score.get('player'), score.get('prop_type'), score.get('line'),
               score.get('recommended_side'), score.get('game_id'))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(score)
    return candidates


def _filter_qualifying(candidates: list, min_games: int, confidence_tier: str) -> list:
    return [
        score for score in candidates
        if (score.get('games_played') or 0) >= min_games
        and score.get('confidence_tier') == confidence_tier
    ]


def _build_straight_plays(qualifying: list, min_edge_straight: float) -> list:
    straight_plays: list = []
    seen_players: set = set()
    for score in qualifying:
        if (score.get('edge') or 0) < min_edge_straight:
            continue
        player = score.get('player') or ''
        if player in seen_players:
            continue
        seen_players.add(player)
        straight_plays.append(score)
    return straight_plays


def _market_key(play: dict) -> tuple:
    return (
        play.get('player'),
        play.get('prop_type'),
        play.get('line'),
        play.get('recommended_side'),
        play.get('game_id'),
    )


def _pick_distinct_legs(
    pool: list,
    count: int,
    min_edge: float,
    used_players: set,
) -> list:
    legs = []
    used_games = set()
    for play in pool:
        if len(legs) >= count:
            break
        player = play.get('player') or ''
        game_id = play.get('game_id') or ''
        if (play.get('edge') or 0) < min_edge or player in used_players:
            continue
        if game_id and game_id in used_games:
            continue
        legs.append(play)
        used_games.add(game_id)
        used_players.add(player)
    return legs


def _build_parlay_groups(qualifying: list, straight_plays: list, Bet) -> list:
    used_players = {play.get('player') or '' for play in straight_plays}
    remaining = [
        play for play in qualifying
        if (play.get('edge') or 0) >= AUTO_PICK_MIN_EDGE_2LEG
        and play not in straight_plays
    ]
    groups = []
    budget = AUTO_PICK_MAX_TOTAL - len(straight_plays)
    for size, minimum_edge in (
        (3, AUTO_PICK_MIN_EDGE_3LEG),
        (2, AUTO_PICK_MIN_EDGE_2LEG),
    ):
        while budget >= size:
            legs = _pick_distinct_legs(
                remaining, size, minimum_edge, used_players
            )
            if len(legs) < size:
                break
            groups.append((Bet.generate_parlay_id(), legs))
            remaining = [play for play in remaining if play not in legs]
            budget -= size
    return groups


def _persist_auto_pick(
    user_id: int,
    play: dict,
    today,
    day_start: datetime,
    is_parlay: bool,
    parlay_id: str | None,
    notes_tag: str,
):
    try:
        match_date = datetime.strptime(
            play.get('match_date') or today.isoformat(), '%Y-%m-%d'
        )
    except ValueError:
        match_date = day_start
    bet_obj = Bet(
        user_id=user_id,
        team_a=str(play.get('away_team') or '')[:80] or 'Away',
        team_b=str(play.get('home_team') or '')[:80] or 'Home',
        match_date=match_date,
        bet_amount=10.0,
        outcome=Outcome.PENDING.value,
        american_odds=int(play.get('recommended_odds') or -110),
        is_parlay=is_parlay,
        parlay_id=parlay_id,
        source=BetSource.AUTO_GENERATED.value,
        bet_type=str(play.get('recommended_side') or 'over'),
        over_under_line=None,
        external_game_id=play.get('game_id') or None,
        player_name=str(play.get('player') or '')[:100] or None,
        prop_type=str(play.get('prop_type') or '')[:40] or None,
        prop_line=float(play.get('line') or 0.0),
        notes=notes_tag,
    )
    db.session.add(bet_obj)
    db.session.flush()
    context = _build_auto_pick_context(bet_obj, play)
    if context:
        db.session.add(PickContext(
            bet_id=bet_obj.id,
            context_json=json.dumps(context),
            projected_stat=play.get('projection'),
            projected_edge=play.get('edge'),
            confidence_tier=play.get('confidence_tier'),
        ))
    return bet_obj


def _persist_production_picks(
    user_id: int,
    straight_plays: list,
    parlay_groups: list,
    today,
    day_start: datetime,
) -> tuple[list, set]:
    created = []
    market_keys = set()
    for play in straight_plays:
        created.append(_persist_auto_pick(
            user_id=user_id,
            play=play,
            today=today,
            day_start=day_start,
            is_parlay=False,
            parlay_id=None,
            notes_tag='AUTO_PICK_BUCKET:straight',
        ))
        market_keys.add(_market_key(play))
    for parlay_id, legs in parlay_groups:
        bucket = '3leg_parlay' if len(legs) == 3 else '2leg_parlay'
        for play in legs:
            created.append(_persist_auto_pick(
                user_id=user_id,
                play=play,
                today=today,
                day_start=day_start,
                is_parlay=True,
                parlay_id=parlay_id,
                notes_tag=f'AUTO_PICK_BUCKET:{bucket}',
            ))
            market_keys.add(_market_key(play))
    return created, market_keys


def _paper_play_qualifies(
    play: dict,
    *,
    market_keys: set,
    paper_players: set,
    min_edge: float,
    max_edge: float,
    min_tier_rank: int,
    tier_rank: dict,
) -> bool:
    player = str(play.get('player') or '')
    edge = abs(float(play.get('edge') or 0.0))
    confidence_rank = int(
        tier_rank.get(str(play.get('confidence_tier') or 'no_edge'), 0)
    )
    return (
        _market_key(play) not in market_keys
        and bool(player)
        and player not in paper_players
        and min_edge <= edge < max_edge
        and confidence_rank >= min_tier_rank
    )


def _persist_paper_picks(
    user_id: int,
    candidates: list,
    market_keys: set,
    today,
    day_start: datetime,
) -> tuple[list, dict[str, int]]:
    if not AUTO_PAPER_ENABLED or AUTO_PAPER_MAX_PER_COHORT <= 0:
        return [], {}
    tier_rank = {'no_edge': 0, 'slight': 1, 'moderate': 2, 'strong': 3}
    created = []
    counts = {}
    paper_players = set()
    for cohort in AUTO_PAPER_COHORTS:
        cohort_name = str(cohort.get('name') or '').strip()
        if not cohort_name:
            continue
        cohort_count = 0
        for play in candidates:
            if cohort_count >= AUTO_PAPER_MAX_PER_COHORT:
                break
            qualifies = _paper_play_qualifies(
                play,
                market_keys=market_keys,
                paper_players=paper_players,
                min_edge=float(cohort.get('min_edge', 0.0) or 0.0),
                max_edge=float(cohort.get('max_edge', 1.0) or 1.0),
                min_tier_rank=tier_rank.get(
                    str(cohort.get('min_tier') or 'slight'), 1
                ),
                tier_rank=tier_rank,
            )
            if not qualifies:
                continue
            created.append(_persist_auto_pick(
                user_id=user_id,
                play=play,
                today=today,
                day_start=day_start,
                is_parlay=False,
                parlay_id=None,
                notes_tag=f'AUTO_PAPER_COHORT:{cohort_name}',
            ))
            market_keys.add(_market_key(play))
            paper_players.add(str(play.get('player') or ''))
            cohort_count += 1
        counts[cohort_name] = cohort_count
    return created, counts


def generate_daily_auto_picks(app):
    from app.services.projection_engine import ProjectionEngine
    from app.services.value_detector import ValueDetector

    with app.app_context():
        today = datetime.now(ET).date()
        day_start = datetime.combine(today, dt_time.min)
        day_end = day_start + timedelta(days=1)

        system_user = _ensure_autopicks_user(db, User)

        existing_today = (
            Bet.query
            .filter(Bet.user_id == system_user.id)
            .filter(Bet.source == BetSource.AUTO_GENERATED.value)
            .filter(Bet.match_date >= day_start, Bet.match_date < day_end)
            .count()
        )
        if existing_today > 0:
            logger.info("Auto picks already generated for %s (%d bets).", today.isoformat(), existing_today)
            db.session.commit()
            return

        detector = ValueDetector(ProjectionEngine())
        scores = detector.score_all_todays_props()

        all_candidates = _build_candidates(
            scores, AUTO_PAPER_MIN_GAMES, require_verified=True, today=today,
        )
        all_qualifying = _filter_qualifying(
            all_candidates, AUTO_PICK_MIN_GAMES, AUTO_PICK_CONFIDENCE_TIER,
        )

        if not all_candidates:
            logger.info("Auto pick generation skipped: no qualifying plays today.")
            db.session.commit()
            return

        straight_plays = _build_straight_plays(
            all_qualifying, AUTO_PICK_MIN_EDGE_STRAIGHT
        )[:AUTO_PICK_MAX_TOTAL]
        parlay_groups = _build_parlay_groups(all_qualifying, straight_plays, Bet)

        if not straight_plays and not parlay_groups:
            logger.info(
                "Auto pick generation skipped: %d plays scored but none met edge/history thresholds.",
                len(all_qualifying),
            )
            db.session.commit()
            return

        created_bets, persisted_market_keys = _persist_production_picks(
            user_id=system_user.id,
            straight_plays=straight_plays,
            parlay_groups=parlay_groups,
            today=today,
            day_start=day_start,
        )
        paper_bets, paper_counts = _persist_paper_picks(
            user_id=system_user.id,
            candidates=all_candidates,
            market_keys=persisted_market_keys,
            today=today,
            day_start=day_start,
        )
        created_bets.extend(paper_bets)

        db.session.commit()
        logger.info(
            "Generated %d auto picks for %s — %d straight | %d parlays (%d 3-leg, %d 2-leg) | paper=%s",
            len(created_bets),
            today.isoformat(),
            len(straight_plays),
            len(parlay_groups),
            sum(1 for _, legs in parlay_groups if len(legs) == 3),
            sum(1 for _, legs in parlay_groups if len(legs) == 2),
            paper_counts or {},
        )
