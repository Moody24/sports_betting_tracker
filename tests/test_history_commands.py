"""Tests for the historical game-log backfill CLI."""

from datetime import date
from unittest.mock import patch

import pandas as pd

from tests.helpers import BaseTestCase


def _league_log_df():
    """Two players, one game — column names match nba_api LeagueGameLog."""
    return pd.DataFrame([
        {
            'PLAYER_ID': 2544, 'PLAYER_NAME': 'LeBron James',
            'TEAM_ABBREVIATION': 'LAL', 'GAME_ID': '0022500001',
            'GAME_DATE': '2025-10-21', 'MATCHUP': 'LAL vs. BOS', 'WL': 'W',
            'MIN': 36, 'PTS': 28, 'REB': 7, 'AST': 11, 'STL': 1, 'BLK': 0,
            'TOV': 3, 'FGM': 10, 'FGA': 19, 'FG3M': 2, 'FG3A': 6,
            'FTM': 6, 'FTA': 7, 'PLUS_MINUS': 12,
        },
        {
            'PLAYER_ID': 1628369, 'PLAYER_NAME': 'Jayson Tatum',
            'TEAM_ABBREVIATION': 'BOS', 'GAME_ID': '0022500001',
            'GAME_DATE': '2025-10-21', 'MATCHUP': 'BOS @ LAL', 'WL': 'L',
            'MIN': 38, 'PTS': 33, 'REB': 9, 'AST': 5, 'STL': 2, 'BLK': 1,
            'TOV': 2, 'FGM': 12, 'FGA': 24, 'FG3M': 4, 'FG3A': 11,
            'FTM': 5, 'FTA': 5, 'PLUS_MINUS': -12,
        },
    ])


class TestSeasonHelpers(BaseTestCase):

    def test_recent_seasons_mid_offseason(self):
        from app.cli.history_commands import _recent_seasons
        # July 2026 → most recent completed/active season is 2025-26
        self.assertEqual(
            _recent_seasons(3, today=date(2026, 7, 7)),
            ['2025-26', '2024-25', '2023-24'],
        )

    def test_recent_seasons_after_october_rolls_forward(self):
        from app.cli.history_commands import _recent_seasons
        self.assertEqual(
            _recent_seasons(2, today=date(2026, 11, 1)),
            ['2026-27', '2025-26'],
        )


class TestRowsFromLeagueLog(BaseTestCase):

    def test_maps_columns_and_derives_context(self):
        from app.cli.history_commands import _rows_from_league_log
        rows = _rows_from_league_log(_league_log_df(), season='2025-26')
        self.assertEqual(len(rows), 2)
        lebron = rows[0]
        self.assertEqual(lebron['player_id'], '2544')
        self.assertEqual(lebron['sport'], 'nba')
        self.assertEqual(lebron['opp_abbr'], 'BOS')
        self.assertEqual(lebron['home_away'], 'HOME')
        self.assertEqual(lebron['game_date'], date(2025, 10, 21))
        self.assertEqual(lebron['stats']['pts'], 28.0)
        self.assertEqual(lebron['stats']['minutes'], 36.0)
        tatum = rows[1]
        self.assertEqual(tatum['home_away'], 'AWAY')
        self.assertEqual(tatum['opp_abbr'], 'LAL')


class TestBackfillCommand(BaseTestCase):

    def _run(self, args):
        runner = self.app.test_cli_runner()
        return runner.invoke(args=['backfill-logs'] + args)

    @patch('app.cli.history_commands._fetch_league_log_df')
    def test_backfill_inserts_and_logs(self, mock_fetch):
        from app.models import HistoricalGameLog, JobLog
        mock_fetch.return_value = _league_log_df()
        result = self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(HistoricalGameLog.query.count(), 2)
            job = JobLog.query.filter_by(job_name='backfill-logs').one()
            self.assertEqual(job.status, 'success')

    @patch('app.cli.history_commands._fetch_league_log_df')
    def test_backfill_is_idempotent(self, mock_fetch):
        from app.models import HistoricalGameLog
        mock_fetch.return_value = _league_log_df()
        self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        result = self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(HistoricalGameLog.query.count(), 2)

    def test_non_nba_sport_rejected_for_now(self):
        result = self._run(['--sport', 'mlb', '--seasons', '1'])
        self.assertNotEqual(result.exit_code, 0)

    @patch('app.cli.history_commands._fetch_league_log_df')
    def test_season_fetch_failure_marks_job_failed(self, mock_fetch):
        from app.models import JobLog
        mock_fetch.side_effect = RuntimeError('nba_api down')
        result = self._run(['--sport', 'nba', '--seasons', '1', '--sleep', '0'])
        self.assertEqual(result.exit_code, 0)  # command reports, doesn't crash
        with self.app.app_context():
            job = JobLog.query.filter_by(job_name='backfill-logs').one()
            self.assertEqual(job.status, 'failed')
            self.assertIn('nba_api down', job.message)
