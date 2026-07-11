"""Import historical betting lines (Kaggle CSV) into HistoricalGameOdds.

Source dataset validated 2026-07-10: 100% join rate vs HistoricalGameLog
on (date, home team) for the overlapping seasons; abbrs are ESPN aliases.
"""

import logging
from datetime import datetime, timezone

import click

from app import db
from app.models import HistoricalGameLog, HistoricalGameOdds, JobLog
from app.services.espn_mapping import normalize_abbr

logger = logging.getLogger(__name__)


def _norm(abbr) -> str:
    return normalize_abbr(str(abbr).strip().upper())


def _f(v):
    """NaN-safe float coercion; returns None for NaN/unparsable values."""
    try:
        v = float(v)
        return None if v != v else v      # NaN -> None
    except (TypeError, ValueError):
        return None


def import_betting_lines(file: str, seasons_from: int = 2024) -> dict:
    """Idempotent import; returns counters (see tests for keys)."""
    import pandas as pd
    job = JobLog(job_name='import-betting-lines',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()

    inserted = skipped = matched = unmatched = score_mm = 0
    errors: list[str] = []
    try:
        df = pd.read_csv(file)
        df = df[df['season'] >= seasons_from]

        existing = {(o.game_date, o.home_abbr)
                    for o in HistoricalGameOdds.query.all()}
        # home-side store games for espn match + score cross-check:
        # one representative row per (date, home team)
        store = {}
        for r in HistoricalGameLog.query.filter_by(
                sport='nba', home_away='HOME').all():
            store.setdefault((r.game_date, r.team_abbr), r)

        batch = []
        for rec in df.to_dict('records'):
            try:
                game_date = datetime.strptime(
                    str(rec['date']), '%Y-%m-%d').date()
            except ValueError:
                errors.append(f"bad date: {rec.get('date')!r}")
                continue
            home, away = _norm(rec['home']), _norm(rec['away'])
            if (game_date, home) in existing:
                skipped += 1
                continue
            spread, total = _f(rec.get('spread')), _f(rec.get('total'))
            if spread is None or total is None:
                errors.append(f"missing spread/total: {game_date} {home}")
                continue
            match = store.get((game_date, home))
            espn_id = match.game_id if match else None
            if match:
                matched += 1
                st = match.stats or {}
                if ('team_score' in st
                        and (float(st['team_score']) != float(rec['score_home'])
                             or float(st.get('opp_score', -1))
                             != float(rec['score_away']))):
                    score_mm += 1
                    logger.warning(
                        "import-betting-lines: score mismatch %s %s",
                        game_date, home)
            else:
                unmatched += 1
            batch.append(HistoricalGameOdds(
                game_date=game_date, home_abbr=home, away_abbr=away,
                spread=spread, favored=str(rec['whos_favored']),
                total=total,
                moneyline_home=_f(rec.get('moneyline_home')),
                moneyline_away=_f(rec.get('moneyline_away')),
                is_playoff=bool(rec.get('playoffs')),
                espn_game_id=espn_id,
            ))
            existing.add((game_date, home))
            inserted += 1
        db.session.add_all(batch)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        errors.append(str(exc))
        logger.error("import-betting-lines failed: %s", exc)
    finally:
        job.finished_at = datetime.now(timezone.utc)
        job.status = 'failed' if errors else 'success'
        job.message = (f"inserted={inserted} skipped={skipped} "
                       f"matched={matched} unmatched={unmatched} "
                       f"score_mismatches={score_mm}"
                       + (f" errors={'; '.join(errors)}" if errors else ""))
        db.session.commit()
    return {'inserted': inserted, 'skipped': skipped, 'matched': matched,
            'unmatched': unmatched, 'score_mismatches': score_mm,
            'errors': errors}


@click.command('import-betting-lines')
@click.option('--file', 'file_path', required=True)
@click.option('--seasons-from', default=2024, show_default=True, type=int,
              help='Kaggle season end-year floor (2024 = our 2023-24).')
def cli_import_betting_lines(file_path, seasons_from):
    """Import historical closing lines from the Kaggle CSV."""
    result = import_betting_lines(file_path, seasons_from=seasons_from)
    click.echo(
        f"Done: inserted={result['inserted']} skipped={result['skipped']} "
        f"matched={result['matched']} unmatched={result['unmatched']} "
        f"score_mismatches={result['score_mismatches']}"
        + (f" errors={'; '.join(result['errors'])}" if result['errors'] else ""))


def register_odds_import_commands(app):
    app.cli.add_command(cli_import_betting_lines)
