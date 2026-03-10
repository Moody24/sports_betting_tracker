"""Background job scheduler using APScheduler.

Jobs run inside the Flask app process on Railway.  Each job function
creates its own app context since they execute on background threads.
"""

import fcntl
import json
import logging
import os
import random
import secrets
from datetime import datetime, timezone, timedelta, time as dt_time
from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:  # pragma: no cover - handled in environments without optional deps
    BackgroundScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)

APP_TIMEZONE = "US/Eastern"
AUTO_PICK_MAX_TOTAL = 50            # hard cap on total bets per day
AUTO_PICK_MIN_EDGE_STRAIGHT = 0.08  # straight bet: ≥8% edge required
AUTO_PICK_MIN_EDGE_2LEG = 0.05      # 2-leg parlay leg: ≥5% edge required
AUTO_PICK_MIN_EDGE_3LEG = 0.08      # 3-leg parlay leg: ≥8% edge required
AUTO_PICK_MIN_GAMES = 15            # minimum game log history for any pick

scheduler = BackgroundScheduler(timezone=APP_TIMEZONE) if BackgroundScheduler else None


_scheduler_lock_fd = None
STALE_JOB_MINUTES = 180

# App instance captured in init_scheduler and reused by all job functions so
# they don't each spin up a fresh Flask app + connection pool on every run.
_scheduler_app = None


def _get_app():
    """Return the shared scheduler app, falling back to create_app() for
    one-off invocations (CLI, tests) where init_scheduler was never called."""
    if _scheduler_app is not None:
        return _scheduler_app
    from app import create_app
    return create_app()


def _acquire_scheduler_lock(lock_path='/tmp/sports_betting_scheduler.lock'):
    """Ensure only one process in the container starts APScheduler."""
    global _scheduler_lock_fd

    if _scheduler_lock_fd is not None:
        return True

    lock_fd = open(lock_path, 'w', encoding='utf-8')
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fd.close()
        return False

    _scheduler_lock_fd = lock_fd
    return True



def _log_job(job_name, func):
    """Wrapper that logs job execution to the JobLog table."""
    from app import db
    from app.models import JobLog

    app = _get_app()
    with app.app_context():
        _close_stale_running_jobs(db, JobLog)
        log_entry = JobLog(
            job_name=job_name,
            started_at=datetime.now(timezone.utc),
            status='running',
        )
        db.session.add(log_entry)
        db.session.commit()
        log_id = log_entry.id

    try:
        func()
        with app.app_context():
            entry = db.session.get(JobLog, log_id)
            if entry:
                entry.finished_at = datetime.now(timezone.utc)
                entry.status = 'success'
                db.session.commit()
    except Exception as exc:
        logger.error("Job %s failed: %s", job_name, exc)
        with app.app_context():
            entry = db.session.get(JobLog, log_id)
            if entry:
                entry.finished_at = datetime.now(timezone.utc)
                entry.status = 'failed'
                entry.message = str(exc)[:500]
                db.session.commit()


def _close_stale_running_jobs(db, JobLog):
    """Mark stale running jobs as failed so the log table stays accurate."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=STALE_JOB_MINUTES)
    stale = (
        JobLog.query
        .filter_by(status='running')
        .filter(JobLog.started_at.isnot(None))
        .all()
    )

    stale_count = 0
    for row in stale:
        started = row.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if not started or started > cutoff:
            continue
        row.status = 'failed'
        row.finished_at = now
        row.message = (
            f"Marked stale after {STALE_JOB_MINUTES} minutes without completion"
        )
        stale_count += 1

    if stale_count:
        db.session.commit()
        logger.warning("Marked %d stale running job log(s) as failed", stale_count)


def refresh_player_stats():
    """Refresh player logs from completed games, with optional NBA API supplement."""
    app = _get_app()
    with app.app_context():
        from app.services.nba_service import get_todays_games
        from app.services.stats_service import refresh_completed_game_logs, update_player_logs_for_games

        completed_summary = refresh_completed_game_logs(days_back=2)
        logger.info(
            "Completed-game refresh: final_games=%d players=%d inserted=%d updated=%d",
            completed_summary.get('final_games_seen', 0),
            completed_summary.get('players_upserted', 0),
            completed_summary.get('rows_inserted', 0),
            completed_summary.get('rows_updated', 0),
        )

        if os.getenv('ENABLE_NBA_API_PLAYER_REFRESH', 'false').lower() != 'true':
            _capture_todays_snapshots(prefetch_props=True)
            return

        games = get_todays_games()
        count = update_player_logs_for_games(games)
        logger.info("Supplemental NBA API slate refresh for %d players", count)
        _capture_todays_snapshots(prefetch_props=True)


def refresh_defense_data():
    """Update team defensive profiles."""
    app = _get_app()
    with app.app_context():
        from app.services.matchup_service import refresh_all_team_defense

        count = refresh_all_team_defense()
        logger.info("Refreshed defense data for %d teams", count)


def refresh_injury_reports():
    """Pull latest injury designations."""
    app = _get_app()
    with app.app_context():
        from app.services.context_service import refresh_injuries

        count = refresh_injuries()
        logger.info("Refreshed %d injury reports", count)


def clear_daily_caches():
    """Clear process-level schedule/context caches at the start of a new game day.

    This ensures the scoreboard and rest-context caches do not carry yesterday's
    data forward when a new day begins.
    """
    from app.services.context_service import clear_schedule_caches
    from app.services.score_cache import invalidate_scores
    from app.services.matchup_service import invalidate_team_defense_cache
    clear_schedule_caches()
    invalidate_scores()
    invalidate_team_defense_cache()
    logger.info("Daily schedule caches cleared")


def run_projections():
    """Generate projections and value scores for all available props."""
    app = _get_app()
    with app.app_context():
        from app.services.projection_engine import ProjectionEngine
        from app.services.value_detector import ValueDetector
        # Clear stale rest-context caches before each scheduled scoring run
        # so back-to-back / days_rest reflect today's actual slate.
        clear_daily_caches()

        _capture_todays_snapshots(prefetch_props=True)
        engine = ProjectionEngine()
        detector = ValueDetector(engine)
        plays = detector.score_all_todays_props()
        strong = [p for p in plays if p.get('edge', 0) > 0.15]
        logger.info(
            "Projections complete: %d total props, %d strong value plays",
            len(plays), len(strong),
        )

        # Invalidate the shared score cache so the next page request picks
        # up freshly computed scores rather than serving stale cached data.
        from app.services.score_cache import invalidate_scores
        invalidate_scores()


def _capture_todays_snapshots(prefetch_props: bool = True):
    """Upsert today's game snapshots and optionally lock props while event ids exist."""
    from app import db
    from app.models import GameSnapshot
    from app.services.nba_service import (
        APP_TIMEZONE as NBA_APP_TIMEZONE,
        get_todays_games,
        fetch_player_props_for_event,
    )

    today = datetime.now(NBA_APP_TIMEZONE).date()
    games = get_todays_games()
    if not games:
        logger.info("Snapshot capture skipped: no games from scoreboard.")
        return

    captured_props = 0
    for game in games:
        snap = GameSnapshot.query.filter_by(
            espn_id=game['espn_id'], game_date=today
        ).first()
        if snap is None:
            snap = GameSnapshot(
                espn_id=game['espn_id'],
                game_date=today,
                home_team=game['home']['name'],
                away_team=game['away']['name'],
                home_logo=game['home'].get('logo', ''),
                away_logo=game['away'].get('logo', ''),
                home_score=game['home']['score'],
                away_score=game['away']['score'],
                status=game['status'],
                over_under_line=game.get('over_under_line'),
                moneyline_home=game.get('moneyline_home'),
                moneyline_away=game.get('moneyline_away'),
                is_final=(game['status'] == 'STATUS_FINAL'),
            )
            db.session.add(snap)
        else:
            snap.home_score = game['home']['score']
            snap.away_score = game['away']['score']
            snap.status = game['status']
            if game['status'] == 'STATUS_FINAL':
                snap.is_final = True
            if not snap.home_logo:
                snap.home_logo = game['home'].get('logo', '')
            if not snap.away_logo:
                snap.away_logo = game['away'].get('logo', '')

        if prefetch_props and snap.props_json is None:
            event_id = (game.get('odds_event_id') or '').strip()
            if event_id:
                props = fetch_player_props_for_event(event_id)
                if props:
                    snap.props_json = json.dumps(props)
                    captured_props += 1

    db.session.commit()
    logger.info(
        "Snapshot capture complete: %d games, %d props snapshots locked.",
        len(games), captured_props,
    )


def _build_auto_pick_context(bet_obj, score: dict) -> dict:
    """Build context payload for auto-generated player prop bets.

    Extracts opponent and team names from the scored prop dict so that
    matchup features (defense rating, pace, back-to-back, etc.) are
    populated instead of being zeroed out.
    """
    from app.services.stats_service import find_player_id
    from app.services.feature_engine import build_pick_context_features

    player_id = find_player_id(bet_obj.player_name or '')
    if not player_id:
        return {}

    selected_odds = int(score.get('recommended_odds') or -110)
    projected_edge = score.get('edge', 0.0)
    if bet_obj.bet_type == 'over':
        projected_edge = score.get('edge_over', projected_edge)
    elif bet_obj.bet_type == 'under':
        projected_edge = score.get('edge_under', projected_edge)

    # Extract real opponent/team context from the scored prop so that
    # matchup features are populated — previously these were always empty,
    # producing zeroed-out defense/pace/B2B features that polluted Model 2.
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


def generate_daily_auto_picks():
    """Generate a separated daily basket of auto picks for faster model learning."""
    from app import db
    from app.enums import BetSource, Outcome
    from app.models import Bet, PickContext, User
    from app.services.projection_engine import ProjectionEngine
    from app.services.value_detector import ValueDetector

    app = _get_app()
    with app.app_context():
        today = datetime.now(ZoneInfo("America/New_York")).date()
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

        # ── Deduplicate and filter to plays with real history and positive edge ──
        seen_keys: set = set()
        scored_players: set = set()   # one bet per player max
        all_qualifying: list = []

        for s in sorted(scores, key=lambda x: x.get('edge', 0), reverse=True):
            if (s.get('games_played') or 0) < AUTO_PICK_MIN_GAMES:
                continue
            key = (s.get('player'), s.get('prop_type'), s.get('line'),
                   s.get('recommended_side'), s.get('game_id'))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_qualifying.append(s)

        if not all_qualifying:
            logger.info("Auto pick generation skipped: no qualifying plays today.")
            db.session.commit()
            return

        # ── Straight bets: one per player, edge ≥ 8% ─────────────────────
        straight_plays: list = []
        for s in all_qualifying:
            if (s.get('edge') or 0) < AUTO_PICK_MIN_EDGE_STRAIGHT:
                continue
            player = s.get('player') or ''
            if player in scored_players:
                continue  # already have a bet on this player
            scored_players.add(player)
            straight_plays.append(s)

        # ── Parlay pool: remaining quality plays not already straight-bet ──
        # Each parlay leg must have edge ≥ 5%; legs come from different games;
        # at most one prop type per player across all parlays.
        parlay_players: set = set(scored_players)  # don't re-use straight-bet players
        parlay_pool: list = [
            s for s in all_qualifying
            if (s.get('edge') or 0) >= AUTO_PICK_MIN_EDGE_2LEG
            and s not in straight_plays
        ]

        def _pick_legs(pool: list, n: int, min_edge: float) -> list:
            """Pick n legs from pool: different games, different players, all ≥ min_edge."""
            legs: list = []
            used_games: set = set()
            for play in pool:
                if len(legs) >= n:
                    break
                if (play.get('edge') or 0) < min_edge:
                    continue
                player = play.get('player') or ''
                game_id = play.get('game_id') or ''
                if player in parlay_players:
                    continue
                if game_id and game_id in used_games:
                    continue
                legs.append(play)
                used_games.add(game_id)
                parlay_players.add(player)
            return legs

        parlay_groups: list = []  # [(parlay_id, [plays])]
        budget = AUTO_PICK_MAX_TOTAL - len(straight_plays)

        # Tier 1: 3-leg parlays (all legs edge ≥ 8%) → highest EV / model signal
        remaining_pool = list(parlay_pool)
        while budget >= 3:
            legs = _pick_legs(remaining_pool, 3, AUTO_PICK_MIN_EDGE_3LEG)
            if len(legs) < 3:
                break
            parlay_groups.append((Bet.generate_parlay_id(), legs))
            for leg in legs:
                remaining_pool.remove(leg)
            budget -= 3

        # Tier 2: 2-leg parlays (both legs edge ≥ 5%)
        while budget >= 2:
            legs = _pick_legs(remaining_pool, 2, AUTO_PICK_MIN_EDGE_2LEG)
            if len(legs) < 2:
                break
            parlay_groups.append((Bet.generate_parlay_id(), legs))
            for leg in legs:
                remaining_pool.remove(leg)
            budget -= 2

        if not straight_plays and not parlay_groups:
            logger.info(
                "Auto pick generation skipped: %d plays scored but none met edge/history thresholds.",
                len(all_qualifying),
            )
            db.session.commit()
            return

        # ── Helper: persist one bet + context ─────────────────────────────
        def _persist_bet(play: dict, is_parlay: bool, parlay_id: str | None, bucket: str) -> Bet:
            match_date = play.get('match_date') or today.isoformat()
            try:
                match_dt = datetime.strptime(match_date, '%Y-%m-%d')
            except ValueError:
                match_dt = day_start
            bet_obj = Bet(
                user_id=system_user.id,
                team_a=str(play.get('away_team') or '')[:80] or 'Away',
                team_b=str(play.get('home_team') or '')[:80] or 'Home',
                match_date=match_dt,
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
                notes=f"AUTO_PICK_BUCKET:{bucket}",
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

        created_bets: list = []

        for play in straight_plays:
            created_bets.append(_persist_bet(play, False, None, 'straight'))

        for pid, legs in parlay_groups:
            n = len(legs)
            bucket = '3leg_parlay' if n == 3 else '2leg_parlay'
            for play in legs:
                created_bets.append(_persist_bet(play, True, pid, bucket))

        db.session.commit()
        logger.info(
            "Generated %d auto picks for %s — %d straight | %d parlays (%d 3-leg, %d 2-leg)",
            len(created_bets),
            today.isoformat(),
            len(straight_plays),
            len(parlay_groups),
            sum(1 for _, legs in parlay_groups if len(legs) == 3),
            sum(1 for _, legs in parlay_groups if len(legs) == 2),
        )


def bootstrap_pick_quality_examples(target_resolved: int = 220, max_logs: int = 10000) -> dict:
    """Backfill hidden resolved auto picks + PickContext for Model 2 bootstrap."""
    from app import db
    from app.enums import BetSource, Outcome
    from app.models import Bet, PickContext, PlayerGameLog, User
    from app.services.feature_engine import build_pick_context_features
    from app.services.stats_service import get_player_stats_summary

    app = _get_app()
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
        created_ctx = 0
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

            # Keep bootstrap samples in the past only.
            if not log.game_date or log.game_date >= datetime.now(timezone.utc).date():
                continue

            prop_type, stat_key, spread = rng.choice(stat_map)
            actual = float(getattr(log, stat_key, 0) or 0)
            if actual < 0:
                continue

            # Use half-lines to avoid push labels.
            base_line = actual + rng.uniform(-spread, spread)
            line = round(base_line) + 0.5
            if line < 0.5:
                line = 0.5

            bet_type = 'over' if rng.random() >= 0.5 else 'under'
            is_win = actual > line if bet_type == 'over' else actual < line
            outcome = Outcome.WIN.value if is_win else Outcome.LOSE.value
            odds = -110 if rng.random() >= 0.3 else int(rng.choice([100, 105, 110, 115, 120]))

            summary = get_player_stats_summary(str(log.player_id), logs=None)
            season_avg = float(summary.get('season', {}).get(stat_key, actual) or actual)
            projected_stat = season_avg + rng.uniform(-1.5, 1.5)
            projected_edge = (projected_stat - line) / max(line, 1.0)
            if bet_type == 'under':
                projected_edge = (line - projected_stat) / max(line, 1.0)
            edge_abs = abs(projected_edge)
            if edge_abs >= 0.15:
                tier = 'strong'
            elif edge_abs >= 0.08:
                tier = 'moderate'
            else:
                tier = 'slight'

            # Extract opponent abbreviation from matchup string (e.g. "LAL vs. BOS"
            # or "LAL @ BOS") so bootstrap rows get real matchup context instead of
            # zeroed-out features that pollute Model 2 training.
            matchup = log.matchup or ''
            is_home = (log.home_away or '').lower() == 'home'
            opponent_abbr = ''
            if ' vs. ' in matchup:
                opponent_abbr = matchup.split(' vs. ', 1)[1].strip()
            elif ' @ ' in matchup:
                opponent_abbr = matchup.split(' @ ', 1)[1].strip()

            context = build_pick_context_features(
                player_name=log.player_name,
                player_id=str(log.player_id),
                prop_type=prop_type,
                prop_line=float(line),
                american_odds=int(odds),
                projected_stat=float(round(projected_stat, 2)),
                projected_edge=float(round(projected_edge, 4)),
                confidence_tier=tier,
                opponent_name=opponent_abbr,
                team_name=str(log.team_abbr or ''),
                is_home=is_home,
            )

            bet_obj = Bet(
                user_id=system_user.id,
                team_a=str(log.team_abbr or 'TEAM'),
                team_b='OPP',
                match_date=datetime.combine(log.game_date, dt_time.min),
                bet_amount=10.0,
                outcome=outcome,
                american_odds=int(odds),
                is_parlay=False,
                source=BetSource.AUTO_GENERATED.value,
                bet_type=bet_type,
                over_under_line=None,
                external_game_id=None,
                player_name=log.player_name,
                prop_type=prop_type,
                prop_line=float(line),
                actual_total=float(actual),
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
            created += 1
            created_ctx += 1

            if created % 100 == 0:
                db.session.commit()

        db.session.commit()
        return {
            'created': created,
            'created_context': created_ctx,
            'existing_resolved': existing_resolved,
            'target_resolved': target_resolved,
            'logs_scanned': scan_count,
        }


def resolve_and_grade():
    """Grade all pending bets using final scores and mark game snapshots final."""
    from app import db
    from app.enums import Outcome
    from app.models import Bet, GameSnapshot
    from app.services.nba_service import resolve_pending_bets, fetch_espn_scoreboard, _STATUS_FINAL

    app = _get_app()
    with app.app_context():
        pending = (
            Bet.query
            .filter_by(outcome=Outcome.PENDING.value)
            .all()
        )
        resolved = resolve_pending_bets(pending)
        for bet_obj, outcome, actual_value in resolved:
            bet_obj.outcome = outcome
            bet_obj.actual_total = actual_value
        db.session.commit()
        logger.info("Graded %d bets", len(resolved))

        # Mark any final-game snapshots as is_final so NBA Today shows them.
        try:
            scoreboards = fetch_espn_scoreboard()
            for game in scoreboards:
                if game.get('status') != _STATUS_FINAL:
                    continue
                snap = GameSnapshot.query.filter_by(espn_id=game['espn_id']).first()
                if snap and not snap.is_final:
                    snap.is_final = True
                    snap.home_score = game['home']['score']
                    snap.away_score = game['away']['score']
            db.session.commit()
        except Exception as exc:
            logger.warning("Snapshot final-mark failed: %s", exc)


def retrain_models():
    """Scheduled model retrain using accumulated game log data."""
    app = _get_app()
    with app.app_context():
        from app.models import ModelMetadata, PlayerGameLog
        from app.services.ml_model import retrain_all_models
        from app.services.pick_quality_model import train_pick_quality_model

        projection_models = (
            ModelMetadata.query
            .filter(ModelMetadata.model_name.like('projection_%'))
            .filter_by(is_active=True)
            .all()
        )

        latest_projection_train = None
        last_logged_rows = None
        if projection_models:
            latest_projection_train = max(
                (m.training_date for m in projection_models if m.training_date),
                default=None,
            )

            # Use points model metadata as canonical snapshot when available.
            points_model = next(
                (m for m in projection_models if m.model_name == 'projection_player_points'),
                None,
            )
            metadata_source = points_model.metadata_json if points_model else projection_models[0].metadata_json
            if metadata_source:
                try:
                    metadata = json.loads(metadata_source)
                    last_logged_rows = metadata.get('player_game_log_rows')
                except (TypeError, ValueError):
                    last_logged_rows = None

        projection_should_train = True

        if latest_projection_train:
            if latest_projection_train.tzinfo is None:
                latest_projection_train = latest_projection_train.replace(tzinfo=timezone.utc)
            days_since_train = (datetime.now(timezone.utc) - latest_projection_train).days
            if days_since_train < 7:
                logger.info(
                    "Skipping projection retrain: latest model is %d day(s) old (< 7 days).",
                    days_since_train,
                )
                projection_should_train = False

        current_rows = PlayerGameLog.query.count()
        last_rows_int = None
        if last_logged_rows is not None:
            try:
                last_rows_int = int(last_logged_rows)
            except (TypeError, ValueError):
                last_rows_int = None

        if last_rows_int is not None and current_rows <= last_rows_int:
            logger.info(
                "Skipping projection retrain: no new PlayerGameLog rows since last training "
                "(current=%d, last=%d).",
                current_rows, last_rows_int,
            )
            projection_should_train = False

        if projection_should_train:
            results = retrain_all_models()
            logger.info("Projection model retrain complete: %s", results)
        else:
            logger.info("Projection model retrain skipped by guardrails.")

        pq_result = train_pick_quality_model()
        logger.info("Pick quality model retrain: %s", pq_result)


def check_model_drift():
    """Check 30-day rolling win-rate against model val_accuracy; log warn if drift > 4%.

    Excludes AUTO_BOOTSTRAP_HIDDEN synthetic bets from the comparison — those rows are
    part of the model's own training set, so including them would make the delta circular.
    Only real bets (manual + real auto picks) are compared against val_accuracy.

    Also reports training-data pollution ratio so drift can be attributed to data
    quality issues rather than model degradation.
    """
    app = _get_app()
    with app.app_context():
        from app import db
        from app.models import Bet, JobLog, ModelMetadata, PickContext
        from app.services.pick_quality_model import _is_polluted_context

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        resolved = (
            db.session.query(Bet, PickContext)
            .join(PickContext, Bet.id == PickContext.bet_id)
            .filter(Bet.outcome.in_(['win', 'lose']))
            .filter(Bet.match_date >= cutoff)
            .filter(
                db.or_(Bet.notes.is_(None), ~Bet.notes.like('AUTO_BOOTSTRAP_HIDDEN%'))
            )
            .all()
        )
        if not resolved:
            logger.info("Drift check: no resolved real bets with context in last 30 days.")
            return

        if len(resolved) < 50:
            logger.info(
                "Drift check: only %d resolved bets in 30 days (need 50+), skipping.",
                len(resolved),
            )
            return

        wins = sum(1 for b, _ in resolved if b.outcome == 'win')
        rolling_rate = wins / len(resolved)

        # Check training-data pollution ratio
        all_training = (
            db.session.query(Bet, PickContext)
            .join(PickContext, Bet.id == PickContext.bet_id)
            .filter(Bet.outcome.in_(['win', 'lose']))
            .all()
        )
        polluted = 0
        for _, pc in all_training:
            try:
                ctx = json.loads(pc.context_json) if pc.context_json else {}
            except (ValueError, TypeError):
                polluted += 1
                continue
            if _is_polluted_context(ctx):
                polluted += 1
        pollution_ratio = polluted / max(len(all_training), 1)

        # Calibration: compare average predicted edge to actual win rate
        avg_edge = 0.0
        edge_count = 0
        for _, ctx in resolved:
            pe = ctx.projected_edge
            if pe is not None:
                avg_edge += float(pe)
                edge_count += 1
        if edge_count > 0:
            avg_edge /= edge_count

        pq_model = ModelMetadata.query.filter_by(
            model_name='pick_quality_nba', is_active=True,
        ).first()
        if not pq_model or not pq_model.val_accuracy:
            logger.info("Drift check: no active pick_quality_nba model for comparison.")
            return

        delta = rolling_rate - pq_model.val_accuracy
        logger.info(
            "Drift check: rolling_rate=%.3f val_accuracy=%.3f delta=%+.3f "
            "avg_edge=%.3f resolved=%d pollution_ratio=%.1f%%",
            rolling_rate, pq_model.val_accuracy, delta, avg_edge,
            len(resolved), pollution_ratio * 100,
        )

        if abs(delta) > 0.04 or pollution_ratio > 0.3:
            parts = []
            if abs(delta) > 0.04:
                parts.append(
                    f"rolling_win_rate={rolling_rate:.3f}, "
                    f"val_accuracy={pq_model.val_accuracy:.3f}, delta={delta:+.3f}"
                )
            if pollution_ratio > 0.3:
                parts.append(
                    f"training_pollution={pollution_ratio:.0%} "
                    f"({polluted}/{len(all_training)} rows have zeroed matchup context)"
                )
            warning_msg = "Model drift detected: " + "; ".join(parts)
            logger.warning(warning_msg)
            now = datetime.now(timezone.utc)
            db.session.add(JobLog(
                job_name='drift_check',
                started_at=now,
                finished_at=now,
                status='warn',
                message=warning_msg[:500],
            ))
            db.session.commit()


def snapshot_props_odds():
    """Snapshot today's player prop odds (FD+DK) for line movement tracking."""
    app = _get_app()
    with app.app_context():
        from app.services.nba_service import snapshot_todays_props
        count = snapshot_todays_props()
        logger.info("snapshot_props_odds: inserted %d rows", count)


def prune_cache():
    """Remove expired and espn_* unresolvable rows from PlayerGameLog."""
    app = _get_app()
    with app.app_context():
        from app.services.stats_service import prune_expired_cache
        result = prune_expired_cache()
        logger.info("Cache prune complete: %s", result)


def init_scheduler(app):
    """Register all scheduled jobs.  Called once from create_app()."""
    global _scheduler_app
    _scheduler_app = app  # reused by all job functions — avoids per-run create_app()

    if scheduler is None or CronTrigger is None:
        logger.warning("APScheduler not installed; background jobs disabled")
        return

    if scheduler.running:
        return

    if not _acquire_scheduler_lock():
        logger.info("Skipping APScheduler startup in this process (lock already held)")
        return

    # Midnight cache reset — clear stale schedule/rest context for the new day
    scheduler.add_job(
        lambda: _log_job('daily_cache_clear', clear_daily_caches),
        CronTrigger(hour=0, minute=1, timezone=APP_TIMEZONE),
        id='daily_cache_clear',
        replace_existing=True,
    )

    # Morning data refresh (10:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('stats_refresh', refresh_player_stats),
        CronTrigger(hour=10, minute=0, timezone=APP_TIMEZONE),
        id='stats_refresh',
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _log_job('defense_refresh', refresh_defense_data),
        CronTrigger(hour=10, minute=15, timezone=APP_TIMEZONE),
        id='defense_refresh',
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _log_job('injury_am', refresh_injury_reports),
        CronTrigger(hour=10, minute=0, timezone=APP_TIMEZONE),
        id='injury_am',
        replace_existing=True,
    )

    # Afternoon injury update (5:00 PM ET)
    scheduler.add_job(
        lambda: _log_job('injury_pm', refresh_injury_reports),
        CronTrigger(hour=17, minute=0, timezone=APP_TIMEZONE),
        id='injury_pm',
        replace_existing=True,
    )

    # Pre-tipoff projections (5:30 PM ET)
    scheduler.add_job(
        lambda: _log_job('projections', run_projections),
        CronTrigger(hour=17, minute=30, timezone=APP_TIMEZONE),
        id='projections',
        replace_existing=True,
    )

    # Daily auto-generated picks for model-2 training (11:45 AM ET)
    scheduler.add_job(
        lambda: _log_job('auto_picks', generate_daily_auto_picks),
        CronTrigger(hour=11, minute=45, timezone=APP_TIMEZONE),
        id='auto_picks',
        replace_existing=True,
    )

    # Overnight bet grading (1:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('grading', resolve_and_grade),
        CronTrigger(hour=1, minute=0, timezone=APP_TIMEZONE),
        id='grading',
        replace_existing=True,
    )

    # Daily model retrain after morning stats refresh (10:30 AM ET)
    scheduler.add_job(
        lambda: _log_job('retrain', retrain_models),
        CronTrigger(hour=10, minute=30, timezone=APP_TIMEZONE),
        id='retrain',
        replace_existing=True,
    )

    # Nightly cache prune: expired rows + espn_* dead rows (2:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('cache_prune', prune_cache),
        CronTrigger(hour=2, minute=0, timezone=APP_TIMEZONE),
        id='cache_prune',
        replace_existing=True,
    )

    # Late-night snapshot update — captures final scores after games end (11:15 PM ET)
    scheduler.add_job(
        lambda: _log_job('snapshot_update', resolve_and_grade),
        CronTrigger(hour=23, minute=15, timezone=APP_TIMEZONE),
        id='snapshot_update',
        replace_existing=True,
    )

    # Weekly drift monitoring (Monday 9:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('drift_check', check_model_drift),
        CronTrigger(day_of_week='mon', hour=9, minute=0, timezone=APP_TIMEZONE),
        id='drift_check',
        replace_existing=True,
    )

    # Odds snapshot every 2 hours (8 AM – 10 PM ET) for line movement tracking
    scheduler.add_job(
        lambda: _log_job('snapshot_props_odds', snapshot_props_odds),
        CronTrigger(hour='8,10,12,14,16,18,20,22', timezone=APP_TIMEZONE),
        id='snapshot_props_odds',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
