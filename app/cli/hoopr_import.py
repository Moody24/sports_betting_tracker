"""Import HistoricalGameLog rows from sportsdataverse hoopR data dumps.

Alternative to ``backfill-logs`` for when stats.nba.com is unreachable
(it silently drops traffic from some networks). The hoopR project
publishes pre-scraped, ESPN-sourced NBA player box scores as parquet
files on GitHub — one bulk download per season, no rate limits.

IMPORTANT — id namespaces: rows imported here carry ESPN ids
(``athlete_id``/ESPN ``game_id``), not stats.nba.com ids. Do not mix
sources within one season: a later ``backfill-logs`` run over the same
season would re-insert the same games under NBA ids and double-count
them for training. The command warns when it detects this.

Unlike ``backfill-logs``, imported rows are born fully enriched: ESPN
box scores include ``starter`` directly, and ``usage_pct`` is computed
from team totals in the same file — so ``enrich-logs`` is not needed.
"""

import logging
import math
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import click

from app import db
from app.cli.history_commands import (
    _norm_player_id,
    _recent_seasons,
    _safe_float,
    _safe_str,
)
from app.models import HistoricalGameLog, JobLog
from app.services.espn_mapping import (
    ESPN_TO_NBA_ABBR as _ESPN_TO_NBA_ABBR,
    NBA_TEAMS as _NBA_TEAMS,
)

logger = logging.getLogger(__name__)

_HOOPR_URL = (
    'https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/'
    'main/nba/player_box/parquet/player_box_{year}.parquet'
)

# hoopR/ESPN season_type codes
_SEASON_TYPE_CODES = {
    'Regular Season': 2,
    'Playoffs': 3,
    'Play-In': 5,
}

# hoopR player_box column → stats-payload key (all coerced to float)
_HOOPR_STAT_COLUMNS = {
    'points': 'pts', 'rebounds': 'reb', 'assists': 'ast',
    'steals': 'stl', 'blocks': 'blk', 'turnovers': 'tov',
    'field_goals_made': 'fgm', 'field_goals_attempted': 'fga',
    'three_point_field_goals_made': 'fg3m',
    'three_point_field_goals_attempted': 'fg3a',
    'free_throws_made': 'ftm', 'free_throws_attempted': 'fta',
    'minutes': 'minutes',
}


def _season_to_hoopr_year(season: str) -> int:
    """App season string → hoopR file year (season END year).

    hoopR names files by the calendar year the season ends in:
    ``player_box_2026.parquet`` covers the 2025-26 season.
    """
    return int(str(season).split('-')[0]) + 1


def _parse_plus_minus(value) -> float:
    """hoopR serializes plus_minus as a signed string ('+12', '-4', '0')."""
    if isinstance(value, str):
        return _safe_float(value.replace('+', '').strip() or None)
    return _safe_float(value)


def _as_date(value):
    """Coerce hoopR game_date (datetime.date or ISO string) to a date."""
    if hasattr(value, 'date') and callable(value.date):   # datetime
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value, '%Y-%m-%d').date()
    return value


def _load_player_box_df(year: int, from_dir: str | None = None):
    """One season of hoopR player box scores, downloaded or read locally."""
    import pandas as pd
    if from_dir:
        return pd.read_parquet(
            Path(from_dir) / f'player_box_{year}.parquet')
    import requests
    resp = requests.get(_HOOPR_URL.format(year=year), timeout=120)
    resp.raise_for_status()
    return pd.read_parquet(BytesIO(resp.content))


def _rows_from_player_box(df, season: str, season_type_code: int,
                          max_games: int | None = None,
                          ) -> tuple[list[dict], dict[str, int]]:
    """Map a hoopR player_box frame to HistoricalGameLog constructor kwargs.

    Skips DNP rows (all stats null) and rows of other season types.
    ``usage_pct`` is derived per player from team totals within the same
    frame: (FGA + 0.44*FTA + TOV) * (TeamMin/5) / (Min * team equivalent).

    Rows whose team doesn't resolve to one of the 30 NBA franchises are
    dropped — ESPN codes All-Star exhibitions as regular season
    (STARS/STRIPES/WORLD teams). Returns ``(rows, dropped)`` where
    ``dropped`` maps each unknown abbreviation to its distinct-game count
    so nothing disappears silently.
    """
    # astype(bool): on object-dtype columns `~` is integer bitwise-NOT
    # (~True == -2, truthy), which would let DNP rows leak through.
    played = df[
        (df['season_type'] == season_type_code)
        & (~df['did_not_play'].astype(bool))
    ].copy()
    for col in ('team_abbreviation', 'opponent_team_abbreviation'):
        played[col] = played[col].replace(_ESPN_TO_NBA_ABBR)
    known = (played['team_abbreviation'].isin(_NBA_TEAMS)
             & played['opponent_team_abbreviation'].isin(_NBA_TEAMS))
    dropped: dict[str, int] = {}
    for col in ('team_abbreviation', 'opponent_team_abbreviation'):
        bad = played[~played[col].isin(_NBA_TEAMS)]
        for abbr, games in bad.groupby(col)['game_id'].nunique().items():
            dropped[abbr] = max(dropped.get(abbr, 0), int(games))
    played = played[known]
    if max_games is not None:
        keep = (
            played[['game_id', 'game_date']].drop_duplicates('game_id')
            .sort_values(['game_date', 'game_id'])['game_id']
            .head(max_games)
        )
        played = played[played['game_id'].isin(set(keep))]

    team_totals = played.groupby(['game_id', 'team_id'])[
        ['minutes', 'field_goals_attempted', 'free_throws_attempted',
         'turnovers']
    ].sum()

    rows = []
    for rec in played.to_dict('records'):
        stats = {
            key: _safe_float(rec.get(col))
            for col, key in _HOOPR_STAT_COLUMNS.items()
        }
        stats['plus_minus'] = _parse_plus_minus(rec.get('plus_minus'))

        tm = team_totals.loc[(rec['game_id'], rec['team_id'])]
        minutes = stats['minutes']
        denom = minutes * (
            tm['field_goals_attempted'] + 0.44 * tm['free_throws_attempted']
            + tm['turnovers']
        )
        if denom > 0:
            chances = (
                stats['fga'] + 0.44 * stats['fta'] + stats['tov']
            ) * (tm['minutes'] / 5)
            usage = chances / denom
        else:
            usage = 0.0
        stats['usage_pct'] = 0.0 if math.isnan(usage) else usage

        rows.append(dict(
            sport='nba',
            player_id=_norm_player_id(rec.get('athlete_id')),
            player_name=_safe_str(rec.get('athlete_display_name')),
            team_abbr=_safe_str(rec.get('team_abbreviation')) or None,
            opp_abbr=_safe_str(rec.get('opponent_team_abbreviation')) or None,
            game_id=_safe_str(rec.get('game_id')),
            game_date=_as_date(rec.get('game_date')),
            season=season,
            home_away=_safe_str(rec.get('home_away')).upper() or None,
            win_loss='W' if bool(rec.get('team_winner')) else 'L',
            starter=bool(rec.get('starter')),
            stats=stats,
        ))
    return rows, dropped


@click.command('import-hoopr-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--seasons', default=3, show_default=True, type=int)
@click.option('--season-type', default='Regular Season', show_default=True,
              type=click.Choice(sorted(_SEASON_TYPE_CODES)))
@click.option('--from-dir', default=None,
              help='Read player_box_{year}.parquet files from a local '
                   'directory instead of downloading from GitHub.')
@click.option('--max-games', default=None, type=int,
              help='Cap games imported per season (whole games kept) — '
                   'for small-batch validation runs.')
def cli_import_hoopr_logs(sport, seasons, season_type, from_dir, max_games):
    """Backfill HistoricalGameLog from hoopR (ESPN) data dumps on GitHub."""
    if sport != 'nba':
        raise click.BadParameter(
            f"sport '{sport}' not supported yet (nba only; mlb/nfl are "
            "Phase 3/4)")
    season_type_code = _SEASON_TYPE_CODES[season_type]

    job = JobLog(job_name='import-hoopr-logs',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()

    inserted = skipped = 0
    errors: list[str] = []

    try:
        for season in _recent_seasons(seasons):
            try:
                df = _load_player_box_df(
                    _season_to_hoopr_year(season), from_dir=from_dir)
            except Exception as exc:
                errors.append(f"{season}: {exc}")
                logger.error("import-hoopr-logs: season %s load failed: %s",
                             season, exc)
                continue

            try:
                existing = {
                    (pid, gid) for pid, gid in db.session.query(
                        HistoricalGameLog.player_id,
                        HistoricalGameLog.game_id,
                    ).filter_by(sport=sport, season=season)
                }
                # NBA game ids are zero-padded ('0022500001'); ESPN ids are
                # bare ints ('401700001'). Same season + both namespaces =
                # the same games counted twice in training data.
                if any(gid.startswith('00') for _, gid in existing):
                    click.echo(
                        f"WARNING: {season} already has stats.nba.com rows; "
                        "importing ESPN rows too would duplicate games. "
                        "New rows are still inserted — clean up one source "
                        "before training.")
                season_rows, dropped = _rows_from_player_box(
                    df, season, season_type_code, max_games=max_games)
                batch = []
                for kwargs in season_rows:
                    if (kwargs['player_id'], kwargs['game_id']) in existing:
                        skipped += 1
                        continue
                    batch.append(HistoricalGameLog(**kwargs))
                db.session.add_all(batch)
                db.session.commit()
                inserted += len(batch)
                msg = f"{season}: +{len(batch)} rows ({skipped} already present)"
                if dropped:
                    msg += f"; dropped non-NBA teams: {dropped}"
                click.echo(msg)
            except Exception as exc:   # malformed rows, DB errors, etc.
                db.session.rollback()
                errors.append(f"{season}: {exc}")
                logger.error("import-hoopr-logs: season %s processing "
                             "failed: %s", season, exc)
                continue
    except BaseException as exc:
        # Ctrl-C or an unexpected bug must still finalize the JobLog row —
        # otherwise it sits at 'running' forever.
        db.session.rollback()
        errors.append(f"aborted: {exc}")
        logger.error("import-hoopr-logs: aborted mid-run: %s", exc)
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


def register_hoopr_import_commands(app):
    app.cli.add_command(cli_import_hoopr_logs)
