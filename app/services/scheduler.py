"""Background job scheduler using APScheduler.

Jobs run inside the Flask app process on Railway.  Each job function
creates its own app context since they execute on background threads.
"""

import fcntl
import logging
from datetime import datetime, timezone

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:  # pragma: no cover - handled in environments without optional deps
    BackgroundScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="US/Eastern") if BackgroundScheduler else None


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
        from app.services.ml_model import retrain_all_models
        from app.services.pick_quality_model import train_pick_quality_model

        results = retrain_all_models()
        logger.info("Projection model retrain complete: %s", results)

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
        CronTrigger(hour=10, minute=0),
        id='stats_refresh',
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _log_job('defense_refresh', refresh_defense_data),
        CronTrigger(hour=10, minute=15),
        id='defense_refresh',
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _log_job('injury_am', refresh_injury_reports),
        CronTrigger(hour=10, minute=0),
        id='injury_am',
        replace_existing=True,
    )

    # Afternoon injury update (5:00 PM ET)
    scheduler.add_job(
        lambda: _log_job('injury_pm', refresh_injury_reports),
        CronTrigger(hour=17, minute=0),
        id='injury_pm',
        replace_existing=True,
    )

    # Pre-tipoff projections (5:30 PM ET)
    scheduler.add_job(
        lambda: _log_job('projections', run_projections),
        CronTrigger(hour=17, minute=30),
        id='projections',
        replace_existing=True,
    )

    # Overnight bet grading (1:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('grading', resolve_and_grade),
        CronTrigger(hour=1, minute=0),
        id='grading',
        replace_existing=True,
    )

    # Weekly model retrain (Sunday 8:00 AM ET)
    scheduler.add_job(
        lambda: _log_job('retrain', retrain_models),
        CronTrigger(day_of_week='sun', hour=8, minute=0),
        id='retrain',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
