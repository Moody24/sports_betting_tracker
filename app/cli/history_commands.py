"""CLI commands for the permanent HistoricalGameLog store."""

import logging
import time
from datetime import datetime, timezone

import click

from app import db
from app.models import HistoricalGameLog, JobLog
from app.services.ml_feature_builder import extract_opp_abbr
from app.utils.data_coercion import normalize_player_id, safe_float, safe_str
from app.utils.seasons import recent_nba_seasons

logger = logging.getLogger(__name__)

# LeagueGameLog column → stats-payload key (all coerced to float)
_NBA_STAT_COLUMNS = {
    'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk',
    'TOV': 'tov', 'FGM': 'fgm', 'FGA': 'fga', 'FG3M': 'fg3m', 'FG3A': 'fg3a',
    'FTM': 'ftm', 'FTA': 'fta', 'MIN': 'minutes', 'PLUS_MINUS': 'plus_minus',
}


def _fetch_league_log_df(season: str, season_type: str):
    """One nba_api call for a full season of player game logs."""
    from nba_api.stats.endpoints import leaguegamelog
    log = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation='P',
        timeout=60,
    )
    return log.get_data_frames()[0]


def _rows_from_league_log(df, season: str) -> list[dict]:
    """Map a LeagueGameLog dataframe to HistoricalGameLog constructor kwargs."""
    rows = []
    for rec in df.to_dict('records'):
        matchup = safe_str(rec.get('MATCHUP'))
        stats = {}
        for col, key in _NBA_STAT_COLUMNS.items():
            stats[key] = safe_float(rec.get(col))
        rows.append(dict(
            sport='nba',
            player_id=normalize_player_id(rec.get('PLAYER_ID')),
            player_name=safe_str(rec.get('PLAYER_NAME')),
            team_abbr=safe_str(rec.get('TEAM_ABBREVIATION')) or None,
            opp_abbr=extract_opp_abbr(matchup) or None,
            game_id=safe_str(rec.get('GAME_ID')),
            game_date=datetime.strptime(
                safe_str(rec.get('GAME_DATE')), '%Y-%m-%d').date(),
            season=season,
            home_away='HOME' if ' vs. ' in matchup else 'AWAY',
            win_loss=safe_str(rec.get('WL')) or None,
            starter=None,          # filled by `flask enrich-logs`
            stats=stats,
        ))
    return rows


@click.command('backfill-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--seasons', default=3, show_default=True, type=int)
@click.option('--season-type', default='Regular Season', show_default=True)
@click.option('--sleep', 'sleep_seconds', default=1.5, show_default=True,
              type=float, help='Pause between season fetches (rate limit).')
def cli_backfill_logs(sport, seasons, season_type, sleep_seconds):
    """Backfill HistoricalGameLog from season-wide league game logs."""
    if sport != 'nba':
        raise click.BadParameter(
            f"sport '{sport}' not supported yet (nba only; mlb/nfl are "
            "Phase 3/4)")

    job = JobLog(job_name='backfill-logs',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()

    inserted = skipped = 0
    errors: list[str] = []

    try:
        for season in recent_nba_seasons(seasons):
            try:
                df = _fetch_league_log_df(season, season_type)
            except Exception as exc:  # nba_api raises assorted exception types
                errors.append(f"{season}: {exc}")
                logger.error("backfill-logs: season %s fetch failed: %s",
                             season, exc)
                continue

            try:
                existing = {
                    (pid, gid) for pid, gid in db.session.query(
                        HistoricalGameLog.player_id,
                        HistoricalGameLog.game_id,
                    ).filter_by(sport=sport, season=season)
                }
                batch = []
                for kwargs in _rows_from_league_log(df, season):
                    if (kwargs['player_id'], kwargs['game_id']) in existing:
                        skipped += 1
                        continue
                    batch.append(HistoricalGameLog(**kwargs))
                db.session.add_all(batch)
                db.session.commit()
                inserted += len(batch)
                click.echo(
                    f"{season}: +{len(batch)} rows ({skipped} already present)")
                if sleep_seconds:
                    time.sleep(sleep_seconds)
            except Exception as exc:  # malformed rows, DB errors, etc.
                db.session.rollback()
                errors.append(f"{season}: {exc}")
                logger.error("backfill-logs: season %s processing failed: %s",
                             season, exc)
                continue
    except BaseException as exc:
        # Ctrl-C during a sleep or an unexpected bug must still finalize the
        # JobLog row — otherwise it sits at 'running' forever.
        db.session.rollback()
        errors.append(f"aborted: {exc}")
        logger.error("backfill-logs: aborted mid-run: %s", exc)
        raise
    finally:
        job.finished_at = datetime.now(timezone.utc)
        job.status = 'failed' if errors else 'success'
        job.message = (
            f"inserted={inserted} skipped={skipped}"
            + (f" errors={'; '.join(errors)}" if errors else "")
        )
        db.session.commit()
        click.echo(f"Done: {job.message}")


def _fetch_advanced_boxscore_df(game_id: str):
    """One nba_api call: advanced box score (USG_PCT, START_POSITION)."""
    from nba_api.stats.endpoints import boxscoreadvancedv2
    box = boxscoreadvancedv2.BoxScoreAdvancedV2(game_id=game_id, timeout=60)
    return box.get_data_frames()[0]   # player-level frame


@click.command('enrich-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--limit', default=200, show_default=True, type=int,
              help='Max games to enrich this run (chunkable).')
@click.option('--sleep', 'sleep_seconds', default=0.8, show_default=True,
              type=float)
def cli_enrich_logs(sport, limit, sleep_seconds):
    """Merge advanced box-score data (usage, starter) into HistoricalGameLog.

    Rows with ``starter IS NULL`` are un-enriched; one API call per game.
    """
    if sport != 'nba':
        raise click.BadParameter(f"sport '{sport}' not supported yet")

    pending_games = [
        gid for (gid,) in db.session.query(HistoricalGameLog.game_id)
        .filter_by(sport=sport)
        .filter(HistoricalGameLog.starter.is_(None))
        .distinct().order_by(HistoricalGameLog.game_id)
        .limit(limit)
    ]
    enriched = failed = 0
    unmatched_games: list[str] = []
    for gid in pending_games:
        try:
            df = _fetch_advanced_boxscore_df(gid)
        except Exception as exc:
            failed += 1
            logger.warning("enrich-logs: game %s fetch failed: %s", gid, exc)
            continue
        by_player = {
            normalize_player_id(rec.get('PLAYER_ID')): rec
            for rec in df.to_dict('records')
        }
        rows = HistoricalGameLog.query.filter_by(
            sport=sport, game_id=gid).all()
        updated = 0
        for row in rows:
            rec = by_player.get(row.player_id)
            if rec is None:
                # Terminal marker: absent from the advanced box score means
                # not a starter. Leaving NULL would put the game back in
                # pending_games forever, burning one API call per run.
                if row.starter is None:
                    row.starter = False
                continue
            row.starter = bool(safe_str(rec.get('START_POSITION')).strip())
            new_stats = dict(row.stats or {})
            new_stats['usage_pct'] = safe_float(rec.get('USG_PCT'))
            row.stats = new_stats   # reassign — JSON columns don't track mutation
            updated += 1
        db.session.commit()
        if updated:
            enriched += 1
        else:
            unmatched_games.append(gid)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    summary = (f"Enriched {enriched} games ({failed} failed, "
               f"{len(pending_games)} attempted)")
    if unmatched_games:
        summary += (f"; {len(unmatched_games)} games had no matching "
                    f"boxscore rows: {', '.join(unmatched_games)}")
    click.echo(summary)


def register_history_commands(app):
    app.cli.add_command(cli_backfill_logs)
    app.cli.add_command(cli_enrich_logs)
