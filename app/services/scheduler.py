"""Background job scheduler using APScheduler.

Jobs run inside the Flask app process on Railway.  Each job function
creates its own app context since they execute on background threads.
"""

import fcntl
import json
import logging
from datetime import datetime, timezone

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:  # pragma: no cover - handled in environments without optional deps
    BackgroundScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)

APP_TIMEZONE = "US/Eastern"

scheduler = BackgroundScheduler(timezone=APP_TIMEZONE) if BackgroundScheduler else None


_scheduler_lock_fd = None


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


def refresh_player_stats():
    """Fetch game logs for all players on today's NBA slate."""
    from app import create_app, db

    app = create_app()
    with app.app_context():
        from app.services.nba_service import get_todays_games
        from app.services.stats_service import update_player_logs_for_games

        games = get_todays_games()
        count = update_player_logs_for_games(games)
        logger.info("Refreshed stats for %d players", count)


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

        engine = ProjectionEngine()
        detector = ValueDetector(engine)
        plays = detector.score_all_todays_props()
        strong = [p for p in plays if p.get('edge', 0) > 0.15]
        logger.info(
            "Projections complete: %d total props, %d strong value plays",
            len(plays), len(strong),
        )


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
            .filter(Bet.external_game_id.isnot(None))
            .all()
        )
        resolved = resolve_pending_bets(pending)
        for bet_obj, outcome, actual_value in resolved:
            bet_obj.outcome = outcome
            bet_obj.actual_total = actual_value
        db.session.commit()
        logger.info("Graded %d bets", len(resolved))


def retrain_models():
    """Weekly model retrain using accumulated game log data."""
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

    # Overnight bet grading (1:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('grading', resolve_and_grade),
        CronTrigger(hour=1, minute=0, timezone=APP_TIMEZONE),
        id='grading',
        replace_existing=True,
    )

    # Weekly model retrain (Sunday 8:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('retrain', retrain_models),
        CronTrigger(day_of_week='sun', hour=8, minute=0, timezone=APP_TIMEZONE),
        id='retrain',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
