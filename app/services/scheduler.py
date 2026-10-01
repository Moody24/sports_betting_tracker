"""APScheduler jobs and their application-context boundary."""

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta

from app.services.auto_pick_service import (
    _build_candidates as _build_candidates,
    _build_parlay_groups as _build_parlay_groups,
    _build_straight_plays as _build_straight_plays,
    _ensure_autopicks_user as _ensure_autopicks_user,
    _filter_qualifying as _filter_qualifying,
    _paper_play_qualifies as _paper_play_qualifies,
    generate_daily_auto_picks as _generate_daily_auto_picks,
)
from app.services.pick_quality_bootstrap import (
    bootstrap_pick_quality_examples as _bootstrap_pick_quality_examples,
)
from app.utils.time_helpers import ET

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
except ModuleNotFoundError:  # pragma: no cover - handled in environments without optional deps
    BackgroundScheduler = None
    CronTrigger = None

logger = logging.getLogger(__name__)

APP_TIMEZONE = "America/New_York"

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


def _acquire_scheduler_lock(lock_path=None):
    """Ensure only one process in the container starts APScheduler."""
    if lock_path is None:
        lock_path = os.path.join(tempfile.gettempdir(), 'sports_betting_scheduler.lock')
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
        logger.error("Job %s failed: %s", job_name, exc, exc_info=True)
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


def generate_daily_auto_picks():
    return _generate_daily_auto_picks(_get_app())


def bootstrap_pick_quality_examples(target_resolved: int = 220, max_logs: int = 10000) -> dict:
    return _bootstrap_pick_quality_examples(
        _get_app(), target_resolved=target_resolved, max_logs=max_logs,
    )


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
        logger.info("Graded %d bets", len(resolved))

        # Generate postmortems for newly settled player-prop legs.
        # Each postmortem is wrapped in a savepoint so a single failure
        # rolls back only that write without aborting the outer transaction.
        from app.services.postmortem_service import create_or_update_postmortem
        pm_count = 0
        for bet_obj, _outcome, _val in resolved:
            try:
                sp = db.session.begin_nested()
                result = create_or_update_postmortem(bet_obj)
                if result is not None:
                    pm_count += 1
                sp.commit()
            except Exception as exc:
                sp.rollback()
                logger.warning("Postmortem error for bet_id=%s: %s", bet_obj.id, exc)
        if pm_count:
            logger.info("Created/updated %d postmortems", pm_count)

        # Mark any final-game snapshots as is_final so NBA Today shows them.
        # Wrapped in a savepoint so snapshot failures don't abort the bet/postmortem writes.
        try:
            sp_snap = db.session.begin_nested()
            scoreboards = fetch_espn_scoreboard()
            for game in scoreboards:
                if game.get('status') != _STATUS_FINAL:
                    continue
                snap = GameSnapshot.query.filter_by(espn_id=game['espn_id']).first()
                if snap and not snap.is_final:
                    snap.is_final = True
                    snap.home_score = game['home']['score']
                    snap.away_score = game['away']['score']
            sp_snap.commit()
        except Exception as exc:
            sp_snap.rollback()
            logger.warning("Snapshot final-mark failed: %s", exc)

        # Single outer commit persists all writes atomically.
        db.session.commit()


def _projection_retrain_allowed(ModelMetadata, PlayerGameLog) -> bool:
    models = (
        ModelMetadata.query
        .filter(ModelMetadata.model_name.like('projection_%'))
        .filter_by(is_active=True)
        .all()
    )
    latest = max(
        (model.training_date for model in models if model.training_date),
        default=None,
    )
    points_model = next(
        (model for model in models
         if model.model_name == 'projection_player_points'),
        None,
    )
    metadata_source = (
        points_model.metadata_json if points_model
        else models[0].metadata_json if models else None
    )
    last_logged_rows = None
    if metadata_source:
        try:
            last_logged_rows = json.loads(metadata_source).get(
                'player_game_log_rows',
            )
        except (TypeError, ValueError):
            pass
    should_train = True
    if latest:
        latest = (latest.replace(tzinfo=timezone.utc)
                  if latest.tzinfo is None else latest)
        days_since_train = (datetime.now(timezone.utc) - latest).days
        if days_since_train < 7:
            logger.info(
                "Skipping projection retrain: latest model is %d day(s) old "
                "(< 7 days).", days_since_train,
            )
            should_train = False
    try:
        previous_rows = (int(last_logged_rows)
                         if last_logged_rows is not None else None)
    except (TypeError, ValueError):
        previous_rows = None
    current_rows = PlayerGameLog.query.count()
    if previous_rows is not None and current_rows <= previous_rows:
        logger.info(
            "Skipping projection retrain: no new PlayerGameLog rows since "
            "last training (current=%d, last=%d).",
            current_rows, previous_rows,
        )
        should_train = False
    return should_train


def retrain_models():
    """Scheduled model retrain using accumulated game log data."""
    app = _get_app()
    with app.app_context():
        from app.models import ModelMetadata, PlayerGameLog
        from app.services.ml_model import retrain_all_models
        from app.services.pick_quality_model import train_pick_quality_model

        if _projection_retrain_allowed(ModelMetadata, PlayerGameLog):
            results = retrain_all_models()
            logger.info("Projection model retrain complete: %s", results)
        else:
            logger.info("Projection model retrain skipped by guardrails.")

        pq_result = train_pick_quality_model()
        logger.info("Pick quality model retrain: %s", pq_result)


def _training_pollution(rows: list, is_polluted) -> tuple[int, float]:
    polluted = 0
    for _, pick_context in rows:
        try:
            context = (json.loads(pick_context.context_json)
                       if pick_context.context_json else {})
        except (ValueError, TypeError):
            polluted += 1
            continue
        polluted += int(is_polluted(context))
    return polluted, polluted / max(len(rows), 1)


def _average_projected_edge(rows: list) -> float:
    edges = [
        float(pick_context.projected_edge)
        for _, pick_context in rows
        if pick_context.projected_edge is not None
    ]
    return sum(edges) / len(edges) if edges else 0.0


def _drift_warning_parts(delta: float, validation_accuracy: float,
                         rolling_rate: float, pollution_ratio: float,
                         polluted: int, total_training: int) -> list[str]:
    parts = []
    if abs(delta) > 0.04:
        parts.append(
            f"rolling_win_rate={rolling_rate:.3f}, "
            f"val_accuracy={validation_accuracy:.3f}, delta={delta:+.3f}"
        )
    if pollution_ratio > 0.3:
        parts.append(
            f"training_pollution={pollution_ratio:.0%} "
            f"({polluted}/{total_training} rows have zeroed matchup context)"
        )
    return parts


def check_model_drift():
    """Check 30-day rolling win-rate against model val_accuracy; log warn if drift > 4%.

    Excludes synthetic/experimental lanes from the comparison:
    - AUTO_BOOTSTRAP_HIDDEN% (synthetic bootstrap)
    - AUTO_PAPER_COHORT:% (shadow paper cohorts)
    Only real bets (manual + production auto picks) are compared against val_accuracy.

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
                db.or_(
                    Bet.notes.is_(None),
                    db.and_(
                        ~Bet.notes.like('AUTO_BOOTSTRAP_HIDDEN%'),
                        ~Bet.notes.like('AUTO_PAPER_COHORT:%'),
                    ),
                )
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
        polluted, pollution_ratio = _training_pollution(
            all_training, _is_polluted_context,
        )
        avg_edge = _average_projected_edge(resolved)

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

        parts = _drift_warning_parts(
            delta, pq_model.val_accuracy, rolling_rate, pollution_ratio,
            polluted, len(all_training),
        )
        if parts:
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


def run_market_governance_job():
    """Weekly market governance cycle: tune thresholds, enforce guardrails, and walk-forward audit."""
    app = _get_app()
    with app.app_context():
        from app.services.market_recommender import run_market_governance

        days = int(os.getenv('MARKET_GOV_DAYS', '180'))
        bins = int(os.getenv('MARKET_GOV_BINS', '5'))
        min_bets = int(os.getenv('MARKET_GOV_MIN_BETS', '20'))
        drift_threshold = float(os.getenv('MARKET_GOV_DRIFT_THRESHOLD', '0.05'))
        train_days = int(os.getenv('MARKET_GOV_TRAIN_DAYS', '60'))
        test_days = int(os.getenv('MARKET_GOV_TEST_DAYS', '14'))
        step_days = int(os.getenv('MARKET_GOV_STEP_DAYS', '14'))
        apply = os.getenv('MARKET_GOV_APPLY', 'true').strip().lower() in ('1', 'true', 'yes', 'on')

        result = run_market_governance(
            days=days,
            bins=bins,
            min_bets=min_bets,
            drift_threshold=drift_threshold,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            apply=apply,
        )
        logger.info(
            "market_governance: tune_selected=%s guard_decisions=%s wf_moneyline=%s wf_total=%s",
            (result.get('tune') or {}).get('selected'),
            (result.get('guard') or {}).get('decisions'),
            (((result.get('walkforward') or {}).get('markets') or {}).get('moneyline') or {}).get('summary'),
            (((result.get('walkforward') or {}).get('markets') or {}).get('total_ou') or {}).get('summary'),
        )


def run_recent_snapshot_backfill_job():
    """Backfill recent final GameSnapshot rows (with bet-derived odds enrichment)."""
    app = _get_app()
    with app.app_context():
        from app.services.nba_service import backfill_game_snapshots

        lookback_days = int(os.getenv('GAME_SNAPSHOT_BACKFILL_DAYS', '45'))
        sleep_seconds = float(os.getenv('GAME_SNAPSHOT_BACKFILL_SLEEP', '0.05'))
        end_date = datetime.now(ET).date()
        start_date = end_date - timedelta(days=max(1, lookback_days))
        result = backfill_game_snapshots(
            start_date=start_date,
            end_date=end_date,
            include_existing=True,
            sleep_seconds=sleep_seconds,
        )
        logger.info("recent_snapshot_backfill: %s", result)


def run_market_coverage_audit_job():
    """Log market data coverage + walk-forward readiness for observability."""
    app = _get_app()
    with app.app_context():
        from app import db
        from app.models import GameSnapshot
        from app.services.market_recommender import walkforward_market_report

        days = int(os.getenv('MARKET_COVERAGE_DAYS', '180'))
        train_days = int(os.getenv('MARKET_COVERAGE_TRAIN_DAYS', '60'))
        test_days = int(os.getenv('MARKET_COVERAGE_TEST_DAYS', '14'))
        step_days = int(os.getenv('MARKET_COVERAGE_STEP_DAYS', '14'))
        cutoff = datetime.now(ET).date() - timedelta(days=days)

        usable_count = (
            db.session.query(GameSnapshot.id)
            .filter(GameSnapshot.game_date >= cutoff)
            .filter(GameSnapshot.is_final.is_(True))
            .filter(GameSnapshot.home_score.isnot(None))
            .filter(GameSnapshot.away_score.isnot(None))
            .filter(GameSnapshot.over_under_line.isnot(None))
            .filter(GameSnapshot.moneyline_home.isnot(None))
            .filter(GameSnapshot.moneyline_away.isnot(None))
            .count()
        )
        wf = walkforward_market_report(
            days=days,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            bins=5,
        )
        logger.info(
            "market_coverage_audit: usable_rows=%d walkforward=%s moneyline=%s total=%s",
            usable_count,
            wf.get('error') or 'ready',
            ((wf.get('markets') or {}).get('moneyline') or {}).get('summary'),
            ((wf.get('markets') or {}).get('total_ou') or {}).get('summary'),
        )


def run_historical_odds_ingest_job():
    """Periodic historical odds ingestion for market backfill."""
    app = _get_app()
    with app.app_context():
        from app.services.nba_service import ingest_historical_market_odds

        lookback_days = int(os.getenv('HISTORICAL_ODDS_INGEST_DAYS', '120'))
        sleep_seconds = float(os.getenv('HISTORICAL_ODDS_INGEST_SLEEP', '0.05'))
        force = os.getenv('HISTORICAL_ODDS_INGEST_FORCE', 'false').strip().lower() in ('1', 'true', 'yes', 'on')
        end_date = datetime.now(ET).date()
        start_date = end_date - timedelta(days=max(1, lookback_days))
        result = ingest_historical_market_odds(
            start_date=start_date,
            end_date=end_date,
            force=force,
            sleep_seconds=sleep_seconds,
        )
        logger.info("historical_odds_ingest: %s", result)


def snapshot_props_odds():
    """Snapshot today's player prop odds (FD+DK) for line movement tracking.

    Guarded: skip entirely when there are no games today (Plan A2) — no
    point burning odds-API quota on an empty slate.
    """
    from app.services.game_day_coordinator import todays_games
    app = _get_app()
    with app.app_context():
        if not todays_games():
            logger.info("snapshot_props_odds: no games today — skipped")
            return
        from app.services.nba_service import snapshot_todays_props
        count = snapshot_todays_props()
        logger.info("snapshot_props_odds: inserted %d rows", count)


def snapshot_event_relative_props():
    """Capture T-60 decision and T-10 closing quotes when an event is due."""
    from app.services.game_day_coordinator import todays_games
    app = _get_app()
    with app.app_context():
        if not todays_games():
            return
        from app.services.nba_service import snapshot_todays_props
        decision = snapshot_todays_props(snapshot_kind='decision')
        closing = snapshot_todays_props(snapshot_kind='close')
        if decision or closing:
            logger.info(
                'snapshot_event_relative_props: decision=%d close=%d', decision, closing,
            )


def prune_cache():
    """Remove expired and espn_* unresolvable rows from PlayerGameLog."""
    app = _get_app()
    with app.app_context():
        from app.services.stats_service import prune_expired_cache
        result = prune_expired_cache()
        logger.info("Cache prune complete: %s", result)


def _update_final_snapshots():
    """Mark completed game snapshots as final without grading bets.

    Runs at 11:15 PM ET — safe to call while West Coast games may still be
    live.  Full bet grading (resolve_and_grade) runs at 1:00 AM once all
    games are finished.
    """
    from app import db
    from app.models import GameSnapshot
    from app.services.nba_service import fetch_espn_scoreboard, _STATUS_FINAL

    app = _get_app()
    with app.app_context():
        try:
            scoreboards = fetch_espn_scoreboard()
            updated = 0
            for game in scoreboards:
                if game.get('status') != _STATUS_FINAL:
                    continue
                snap = GameSnapshot.query.filter_by(espn_id=game['espn_id']).first()
                if snap and not snap.is_final:
                    snap.is_final = True
                    snap.home_score = game['home']['score']
                    snap.away_score = game['away']['score']
                    updated += 1
            db.session.commit()
            logger.info("Snapshot final-mark: %d game(s) marked final", updated)
        except Exception as exc:
            logger.warning("Snapshot final-mark failed: %s", exc)


def _watchdog_check_stale_jobs():
    """Log a warning for any scheduled job that hasn't run in 2x its expected interval."""
    from datetime import timezone as _tz
    now = datetime.now(tz=_tz.utc)
    app = _get_app()
    with app.app_context():
        from app.models import JobLog
        logs = JobLog.query.order_by(JobLog.job_name, JobLog.started_at.desc()).all()
        seen = {}
        for log in logs:
            if log.job_name not in seen:
                seen[log.job_name] = log.started_at
        if scheduler is None:
            return
        for job in scheduler.get_jobs():
            last_run = seen.get(job.id)
            if last_run and hasattr(job, 'trigger'):
                last_run_utc = last_run.replace(tzinfo=_tz.utc) if last_run.tzinfo is None else last_run
                if (now - last_run_utc).total_seconds() > 3600:
                    logger.warning(
                        "Stale job detected: %s last ran at %s", job.id, last_run
                    )


def _run_coordinator_tick():
    from app.services.game_day_coordinator import run_tick
    app = _get_app()
    with app.app_context():
        tier = run_tick()
        logger.info("coordinator tick: %s", tier)


def _run_hoopr_reconcile():
    from app.services.hoopr_import_service import import_hoopr_seasons
    app = _get_app()
    with app.app_context():
        result = import_hoopr_seasons(seasons=1)
        logger.info("hoopr reconcile: %s", result)


def _run_refresh_scenario_splits():
    from app.services.scenario_engine import refresh_splits
    app = _get_app()
    with app.app_context():
        result = refresh_splits()
        logger.info("scenario splits refresh: %s", result)


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
        CronTrigger(hour=10, minute=5, timezone=APP_TIMEZONE),
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

    # Late-night snapshot update — marks final scores without grading bets (11:15 PM ET).
    # West Coast games tip at ~10:30 PM ET and run past midnight — grading mid-game
    # would produce partial stats. Full resolve_and_grade runs at 1:00 AM instead.
    scheduler.add_job(
        lambda: _log_job('snapshot_update', _update_final_snapshots),
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

    # Weekly market governance (Monday 9:20 AM ET):
    # threshold tune + drift/ROI guard + walk-forward stability report.
    scheduler.add_job(
        lambda: _log_job('market_governance', run_market_governance_job),
        CronTrigger(day_of_week='mon', hour=9, minute=20, timezone=APP_TIMEZONE),
        id='market_governance',
        replace_existing=True,
    )

    # Daily historical backfill for recent finished games (3:30 AM ET)
    scheduler.add_job(
        lambda: _log_job('snapshot_backfill', run_recent_snapshot_backfill_job),
        CronTrigger(hour=3, minute=30, timezone=APP_TIMEZONE),
        id='snapshot_backfill',
        replace_existing=True,
    )

    # Weekly market coverage audit (Sunday 9:30 AM ET)
    scheduler.add_job(
        lambda: _log_job('market_coverage_audit', run_market_coverage_audit_job),
        CronTrigger(day_of_week='sun', hour=9, minute=30, timezone=APP_TIMEZONE),
        id='market_coverage_audit',
        replace_existing=True,
    )

    # Weekly historical odds ingest catch-up (Sunday 8:40 AM ET)
    scheduler.add_job(
        lambda: _log_job('historical_odds_ingest', run_historical_odds_ingest_job),
        CronTrigger(day_of_week='sun', hour=8, minute=40, timezone=APP_TIMEZONE),
        id='historical_odds_ingest',
        replace_existing=True,
    )

    # Odds snapshot every 2 hours (8 AM – 10 PM ET) for line movement tracking
    scheduler.add_job(
        lambda: _log_job('snapshot_props_odds', snapshot_props_odds),
        CronTrigger(hour='8,10,12,14,16,18,20,22', timezone=APP_TIMEZONE),
        id='snapshot_props_odds',
        replace_existing=True,
    )

    scheduler.add_job(
        lambda: _log_job('snapshot_event_relative_props', snapshot_event_relative_props),
        CronTrigger(minute='*/5', timezone=APP_TIMEZONE),
        id='snapshot_event_relative_props',
        replace_existing=True,
    )

    # Watchdog: warn if any scheduled job hasn't run in over 60 minutes (every 30 min)
    scheduler.add_job(
        _watchdog_check_stale_jobs,
        trigger=CronTrigger(minute='*/30', timezone=APP_TIMEZONE),
        id='watchdog_stale_jobs',
        replace_existing=True,
    )

    # Plan A2: game-day coordinator — tiered polling + event chains
    scheduler.add_job(
        lambda: _log_job('game_day_coordinator', _run_coordinator_tick),
        CronTrigger(minute='*/5', timezone=APP_TIMEZONE),
        id='game_day_coordinator',
        replace_existing=True,
    )

    # Plan A2: weekly hoopR reconciliation (current season, idempotent)
    scheduler.add_job(
        lambda: _log_job('hoopr_reconcile', _run_hoopr_reconcile),
        CronTrigger(day_of_week='sun', hour=8, minute=20,
                    timezone=APP_TIMEZONE),
        id='hoopr_reconcile',
        replace_existing=True,
    )

    # Plan B: nightly scenario-split materialization (after night's appends)
    scheduler.add_job(
        lambda: _log_job('refresh_scenario_splits',
                         _run_refresh_scenario_splits),
        CronTrigger(hour=5, minute=10, timezone=APP_TIMEZONE),
        id='refresh_scenario_splits',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
