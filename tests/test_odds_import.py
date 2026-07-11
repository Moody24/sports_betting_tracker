"""Tests for the Kaggle betting-lines importer."""

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from tests.helpers import BaseTestCase


def _csv(rows) -> str:
    cols = ['season', 'date', 'regular', 'playoffs', 'away', 'home',
            'score_away', 'score_home', 'whos_favored', 'spread', 'total',
            'moneyline_away', 'moneyline_home']
    path = Path(tempfile.mkdtemp()) / 'lines.csv'
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)
    return str(path)


def _row(**kw):
    base = dict(season=2026, date='2025-10-21', regular=True, playoffs=False,
                away='gs', home='lal', score_away=110, score_home=120,
                whos_favored='home', spread=6.5, total=224.5,
                moneyline_away=200.0, moneyline_home=-240.0)
    base.update(kw)
    return base


class TestImportBettingLines(BaseTestCase):

    def test_imports_normalizes_and_flags(self):
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameOdds
        path = _csv([_row(),
                     _row(date='2026-04-20', regular=False, playoffs=True,
                          away='ny', home='sa', whos_favored='away',
                          spread=2.5, total=215.0)])
        with self.app.app_context():
            result = import_betting_lines(path)
            self.assertEqual(result['inserted'], 2)
            reg = HistoricalGameOdds.query.filter_by(
                game_date=date(2025, 10, 21)).one()
            self.assertEqual(reg.home_abbr, 'LAL')
            self.assertEqual(reg.away_abbr, 'GSW')     # gs normalized
            self.assertEqual(reg.favored, 'home')
            self.assertFalse(reg.is_playoff)
            po = HistoricalGameOdds.query.filter_by(
                game_date=date(2026, 4, 20)).one()
            self.assertEqual(po.home_abbr, 'SAS')
            self.assertTrue(po.is_playoff)

    def test_idempotent_and_seasons_from_filter(self):
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameOdds
        path = _csv([_row(), _row(season=2010, date='2009-11-01',
                                  away='bos', home='mia')])
        with self.app.app_context():
            r1 = import_betting_lines(path, seasons_from=2024)
            self.assertEqual(r1['inserted'], 1)        # 2010 filtered out
            r2 = import_betting_lines(path, seasons_from=2024)
            self.assertEqual(r2['inserted'], 0)
            self.assertEqual(r2['skipped'], 1)
            self.assertEqual(HistoricalGameOdds.query.count(), 1)

    def test_espn_game_match_and_score_crosscheck(self):
        from app import db
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameOdds, HistoricalGameLog
        with self.app.app_context():
            db.session.add(HistoricalGameLog(
                sport='nba', player_id='1966', player_name='LeBron James',
                team_abbr='LAL', opp_abbr='GSW', game_id='401800123',
                game_date=date(2025, 10, 21), season='2025-26',
                home_away='HOME', win_loss='W', starter=True,
                stats={'pts': 28.0, 'team_score': 120.0,
                       'opp_score': 110.0}))
            db.session.commit()
            result = import_betting_lines(_csv([_row()]))
            self.assertEqual(result['matched'], 1)
            self.assertEqual(result['score_mismatches'], 0)
            odds = HistoricalGameOdds.query.one()
            self.assertEqual(odds.espn_game_id, '401800123')

    def test_score_mismatch_reported_not_fatal(self):
        from app import db
        from app.cli.odds_import import import_betting_lines
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(HistoricalGameLog(
                sport='nba', player_id='1966', player_name='LeBron James',
                team_abbr='LAL', opp_abbr='GSW', game_id='401800123',
                game_date=date(2025, 10, 21), season='2025-26',
                home_away='HOME', win_loss='W', starter=True,
                stats={'pts': 28.0, 'team_score': 999.0,
                       'opp_score': 110.0}))
            db.session.commit()
            result = import_betting_lines(_csv([_row()]))
            self.assertEqual(result['inserted'], 1)     # still imported
            self.assertEqual(result['score_mismatches'], 1)

    def test_cli_registered_and_reports(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=[
            'import-betting-lines', '--file', _csv([_row()])])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('inserted=1', result.output)
        self.assertIn('matched=', result.output)
