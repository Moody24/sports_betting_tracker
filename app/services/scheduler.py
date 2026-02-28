"""Background job scheduler using APScheduler.

Jobs run inside the Flask app process on Railway.  Each job function
creates its own app context since they execute on background threads.
"""

import fcntl
import json
import logging
import os
import random
from datetime import datetime, timezone, timedelta, time as dt_time

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:  # pragma: no cover - handled in environments without optional deps
    BackgroundScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)

APP_TIMEZONE = "US/Eastern"
AUTO_PICK_STRONG_COUNT = 3
AUTO_PICK_EV_COUNT = 4
AUTO_PICK_COINFLIP_COUNT = 3
AUTO_PICK_LONGSHOT_PARLAY_LEGS = 3

scheduler = BackgroundScheduler(timezone=APP_TIMEZONE) if BackgroundScheduler else None


_scheduler_lock_fd = None
STALE_JOB_MINUTES = 180


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
    from app import create_app, db
    from app.models import JobLog

    app = create_app()
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
    from app import create_app

    app = create_app()
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
    from app import create_app, db

    app = create_app()
    with app.app_context():
        from app.services.matchup_service import refresh_all_team_defense

        count = refresh_all_team_defense()
        logger.info("Refreshed defense data for %d teams", count)


def refresh_injury_reports():
    """Pull latest injury designations."""
    from app import create_app, db

    app = create_app()
    with app.app_context():
        from app.services.context_service import refresh_injuries

        count = refresh_injuries()
        logger.info("Refreshed %d injury reports", count)


def run_projections():
    """Generate projections and value scores for all available props."""
    from app import create_app, db

    app = create_app()
    with app.app_context():
        from app.services.projection_engine import ProjectionEngine
        from app.services.value_detector import ValueDetector

        _capture_todays_snapshots(prefetch_props=True)
        engine = ProjectionEngine()
        detector = ValueDetector(engine)
        plays = detector.score_all_todays_props()
        strong = [p for p in plays if p.get('edge', 0) > 0.15]
        logger.info(
            "Projections complete: %d total props, %d strong value plays",
            len(plays), len(strong),
        )


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
    """Build context payload for auto-generated player prop bets."""
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

    return build_pick_context_features(
        player_name=bet_obj.player_name or '',
        player_id=str(player_id),
        prop_type=bet_obj.prop_type or '',
        prop_line=float(bet_obj.prop_line or 0.0),
        american_odds=selected_odds,
        projected_stat=float(score.get('projection', 0.0) or 0.0),
        projected_edge=float(projected_edge or 0.0),
        confidence_tier=score.get('confidence_tier', 'no_edge'),
        opponent_name='',
        team_name='',
        is_home=True,
    )


def _ensure_autopicks_user(db, User):
    system_user = User.query.filter_by(username='__autopicks__').first()
    if system_user is None:
        system_user = User(username='__autopicks__', email='autopicks@local.invalid')
        system_user.set_password('auto-picks-system-user')
        db.session.add(system_user)
        db.session.flush()
    return system_user


def generate_daily_auto_picks():
    """Generate a separated daily basket of auto picks for faster model learning."""
    from app import create_app, db
    from app.enums import BetSource, Outcome
    from app.models import Bet, PickContext, User
    from app.services.projection_engine import ProjectionEngine
    from app.services.value_detector import ValueDetector

    app = create_app()
    with app.app_context():
        today = datetime.now().date()
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
        actionable = [s for s in scores if s.get('games_played', 0) >= 10 and s.get('confidence_tier') != 'no_edge']
        strong = [s for s in actionable if s.get('confidence_tier') == 'strong'][:AUTO_PICK_STRONG_COUNT]
        ev_positive = [s for s in actionable if s.get('edge', 0) >= 0.05][:AUTO_PICK_EV_COUNT]
        coin_flip = [s for s in scores if s.get('games_played', 0) >= 10 and abs(s.get('edge', 0)) <= 0.02][:AUTO_PICK_COINFLIP_COUNT]
        longshot_pool = [s for s in actionable if int(s.get('recommended_odds') or 0) >= 120][:AUTO_PICK_LONGSHOT_PARLAY_LEGS]

        selected = []
        seen = set()

        def _add_bucket(bucket_name: str, bucket_scores: list):
            for play in bucket_scores:
                key = (play.get('player'), play.get('prop_type'), play.get('line'), play.get('recommended_side'), play.get('game_id'))
                if key in seen:
                    continue
                seen.add(key)
                selected.append((bucket_name, play))

        _add_bucket('strong', strong)
        _add_bucket('ev_positive', ev_positive)
        _add_bucket('coin_flip', coin_flip)

        if not selected:
            logger.info("Auto pick generation skipped: no playable scores.")
            db.session.commit()
            return

        created_bets = []
        for bucket, play in selected:
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
                is_parlay=False,
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
            created_bets.append(bet_obj)

        # Create one long-shot parlay (2-3 legs) when possible.
        if len(longshot_pool) >= 2:
            parlay_legs = longshot_pool[:3]
            parlay_id = Bet.generate_parlay_id()
            for play in parlay_legs:
                match_date = play.get('match_date') or today.isoformat()
                try:
                    match_dt = datetime.strptime(match_date, '%Y-%m-%d')
                except ValueError:
                    match_dt = day_start
                leg = Bet(
                    user_id=system_user.id,
                    team_a=str(play.get('away_team') or '')[:80] or 'Away',
                    team_b=str(play.get('home_team') or '')[:80] or 'Home',
                    match_date=match_dt,
                    bet_amount=10.0,
                    outcome=Outcome.PENDING.value,
                    american_odds=int(play.get('recommended_odds') or -110),
                    is_parlay=True,
                    parlay_id=parlay_id,
                    source=BetSource.AUTO_GENERATED.value,
                    bet_type=str(play.get('recommended_side') or 'over'),
                    external_game_id=play.get('game_id') or None,
                    player_name=str(play.get('player') or '')[:100] or None,
                    prop_type=str(play.get('prop_type') or '')[:40] or None,
                    prop_line=float(play.get('line') or 0.0),
                    notes="AUTO_PICK_BUCKET:longshot_parlay",
                )
                db.session.add(leg)
                db.session.flush()
                context = _build_auto_pick_context(leg, play)
                if context:
                    db.session.add(PickContext(
                        bet_id=leg.id,
                        context_json=json.dumps(context),
                        projected_stat=play.get('projection'),
                        projected_edge=play.get('edge'),
                        confidence_tier=play.get('confidence_tier'),
                    ))
                created_bets.append(leg)

        db.session.commit()
        logger.info(
            "Generated %d auto picks for %s (strong<=%d, ev<=%d, coinflip<=%d, longshot_parlay_legs<=%d)",
            len(created_bets),
            today.isoformat(),
            AUTO_PICK_STRONG_COUNT,
            AUTO_PICK_EV_COUNT,
            AUTO_PICK_COINFLIP_COUNT,
            AUTO_PICK_LONGSHOT_PARLAY_LEGS,
        )


def bootstrap_pick_quality_examples(target_resolved: int = 220, max_logs: int = 10000) -> dict:
    """Backfill hidden resolved auto picks + PickContext for Model 2 bootstrap."""
    from app import create_app, db
    from app.enums import BetSource, Outcome
    from app.models import Bet, PickContext, PlayerGameLog, User
    from app.services.feature_engine import build_pick_context_features
    from app.services.stats_service import get_player_stats_summary

    app = create_app()
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

            context = build_pick_context_features(
                player_name=log.player_name,
                player_id=str(log.player_id),
                prop_type=prop_type,
                prop_line=float(line),
                american_odds=int(odds),
                projected_stat=float(round(projected_stat, 2)),
                projected_edge=float(round(projected_edge, 4)),
                confidence_tier=tier,
                opponent_name='',
                team_name='',
                is_home=(log.home_away or '').lower() == 'home',
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
    """Grade all pending bets using final scores."""
    from app import create_app, db
    from app.enums import Outcome
    from app.models import Bet
    from app.services.nba_service import resolve_pending_bets

    app = create_app()
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


def retrain_models():
    """Scheduled model retrain using accumulated game log data."""
    from app import create_app, db

    app = create_app()
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


def init_scheduler(app):
    """Register all scheduled jobs.  Called once from create_app()."""
    if scheduler is None or CronTrigger is None:
        logger.warning("APScheduler not installed; background jobs disabled")
        return

    if scheduler.running:
        return

    if not _acquire_scheduler_lock():
        logger.info("Skipping APScheduler startup in this process (lock already held)")
        return

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

    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
