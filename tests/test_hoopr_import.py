"""Tests for the hoopR (ESPN-sourced) HistoricalGameLog import CLI."""

from datetime import date
from unittest.mock import patch

import pandas as pd

from tests.helpers import BaseTestCase


def _player_box_df():
    """One game, two teams, two players each — hoopR player_box columns.

    Includes one DNP row and one playoff row that the regular-season
    import must both skip.
    """
    base = {
        'game_id': 401700001, 'season': 2026, 'season_type': 2,
        'game_date': date(2025, 10, 21), 'did_not_play': False,
    }
    rows = [
        {
            **base, 'athlete_id': 1966,
            'athlete_display_name': 'LeBron James', 'team_id': 13,
            'team_abbreviation': 'LAL', 'opponent_team_abbreviation': 'BOS',
            'home_away': 'home', 'team_winner': True, 'starter': True,
            'minutes': 36.0, 'points': 28.0, 'rebounds': 7.0, 'assists': 11.0,
            'steals': 1.0, 'blocks': 0.0, 'turnovers': 3.0,
            'field_goals_made': 10.0, 'field_goals_attempted': 19.0,
            'three_point_field_goals_made': 2.0,
            'three_point_field_goals_attempted': 6.0,
            'free_throws_made': 6.0, 'free_throws_attempted': 7.0,
            'plus_minus': '+12',
        },
        {
            **base, 'athlete_id': 6583,
            'athlete_display_name': 'Anthony Davis', 'team_id': 13,
            'team_abbreviation': 'LAL', 'opponent_team_abbreviation': 'BOS',
            'home_away': 'home', 'team_winner': True, 'starter': True,
            'minutes': 30.0, 'points': 22.0, 'rebounds': 12.0, 'assists': 3.0,
            'steals': 2.0, 'blocks': 3.0, 'turnovers': 2.0,
            'field_goals_made': 9.0, 'field_goals_attempted': 15.0,
            'three_point_field_goals_made': 0.0,
            'three_point_field_goals_attempted': 1.0,
            'free_throws_made': 4.0, 'free_throws_attempted': 5.0,
            'plus_minus': '+8',
        },
        {
            **base, 'athlete_id': 4065648,
            'athlete_display_name': 'Jayson Tatum', 'team_id': 2,
            'team_abbreviation': 'BOS', 'opponent_team_abbreviation': 'LAL',
            'home_away': 'away', 'team_winner': False, 'starter': True,
            'minutes': 38.0, 'points': 33.0, 'rebounds': 9.0, 'assists': 5.0,
            'steals': 2.0, 'blocks': 1.0, 'turnovers': 2.0,
            'field_goals_made': 12.0, 'field_goals_attempted': 24.0,
            'three_point_field_goals_made': 4.0,
            'three_point_field_goals_attempted': 11.0,
            'free_throws_made': 5.0, 'free_throws_attempted': 5.0,
            'plus_minus': '-12',
        },
        {
            **base, 'athlete_id': 3078576,
            'athlete_display_name': 'Derrick White', 'team_id': 2,
            'team_abbreviation': 'BOS', 'opponent_team_abbreviation': 'LAL',
            'home_away': 'away', 'team_winner': False, 'starter': False,
            'minutes': 32.0, 'points': 14.0, 'rebounds': 4.0, 'assists': 6.0,
            'steals': 1.0, 'blocks': 2.0, 'turnovers': 1.0,
            'field_goals_made': 5.0, 'field_goals_attempted': 12.0,
            'three_point_field_goals_made': 2.0,
            'three_point_field_goals_attempted': 7.0,
            'free_throws_made': 2.0, 'free_throws_attempted': 2.0,
            'plus_minus': '-6',
        },
        # DNP row — all stats null, must be skipped entirely.
        {
            **base, 'athlete_id': 999, 'did_not_play': True,
            'athlete_display_name': 'Bench Guy', 'team_id': 13,
            'team_abbreviation': 'LAL', 'opponent_team_abbreviation': 'BOS',
            'home_away': 'home', 'team_winner': True, 'starter': False,
            'minutes': None, 'points': None, 'rebounds': None,
            'assists': None, 'steals': None, 'blocks': None,
            'turnovers': None, 'field_goals_made': None,
            'field_goals_attempted': None,
            'three_point_field_goals_made': None,
            'three_point_field_goals_attempted': None,
            'free_throws_made': None, 'free_throws_attempted': None,
            'plus_minus': None,
        },
        # All-Star exhibition — ESPN codes these season_type 2 (regular
        # season!), so they must be excluded by team-abbr validation.
        {
            **base, 'game_id': 401777777, 'game_date': date(2026, 2, 15),
            'athlete_id': 1966, 'athlete_display_name': 'LeBron James',
            'team_id': 91, 'team_abbreviation': 'STARS',
            'opponent_team_abbreviation': 'WORLD',
            'home_away': 'home', 'team_winner': True, 'starter': True,
            'minutes': 20.0, 'points': 15.0, 'rebounds': 4.0, 'assists': 6.0,
            'steals': 0.0, 'blocks': 0.0, 'turnovers': 1.0,
            'field_goals_made': 6.0, 'field_goals_attempted': 10.0,
            'three_point_field_goals_made': 2.0,
            'three_point_field_goals_attempted': 4.0,
            'free_throws_made': 1.0, 'free_throws_attempted': 1.0,
            'plus_minus': '+10',
        },
        # Playoff row (season_type 3) — filtered out on a regular-season run.
        {
            **base, 'season_type': 3, 'game_id': 401799999,
            'game_date': date(2026, 4, 20), 'athlete_id': 1966,
            'athlete_display_name': 'LeBron James', 'team_id': 13,
            'team_abbreviation': 'LAL', 'opponent_team_abbreviation': 'DEN',
            'home_away': 'away', 'team_winner': False, 'starter': True,
            'minutes': 40.0, 'points': 30.0, 'rebounds': 8.0, 'assists': 9.0,
            'steals': 1.0, 'blocks': 1.0, 'turnovers': 4.0,
            'field_goals_made': 11.0, 'field_goals_attempted': 22.0,
            'three_point_field_goals_made': 3.0,
            'three_point_field_goals_attempted': 8.0,
            'free_throws_made': 5.0, 'free_throws_attempted': 6.0,
            'plus_minus': '-3',
        },
    ]
    return pd.DataFrame(rows)


class TestSeasonYearMapping(BaseTestCase):

    def test_app_season_string_maps_to_hoopr_end_year(self):
        from app.cli.hoopr_import import _season_to_hoopr_year
        self.assertEqual(_season_to_hoopr_year('2025-26'), 2026)
        self.assertEqual(_season_to_hoopr_year('2024-25'), 2025)


class TestParsePlusMinus(BaseTestCase):

    def test_signed_strings_none_and_numeric(self):
        from app.cli.hoopr_import import _parse_plus_minus
        self.assertEqual(_parse_plus_minus('+12'), 12.0)
        self.assertEqual(_parse_plus_minus('-4'), -4.0)
        self.assertEqual(_parse_plus_minus('0'), 0.0)
        self.assertEqual(_parse_plus_minus(None), 0.0)
        self.assertEqual(_parse_plus_minus(float('nan')), 0.0)
        self.assertEqual(_parse_plus_minus(7), 7.0)


class TestRowsFromPlayerBox(BaseTestCase):

    def _rows(self, **kwargs):
        from app.cli.hoopr_import import _rows_from_player_box
        defaults = dict(season='2025-26', season_type_code=2)
        defaults.update(kwargs)
        rows, _ = _rows_from_player_box(_player_box_df(), **defaults)
        return rows

    def test_espn_team_abbrs_normalized_to_nba_convention(self):
        df = _player_box_df()
        df.loc[df.team_abbreviation == 'BOS', 'team_abbreviation'] = 'GS'
        df.loc[df.opponent_team_abbreviation == 'BOS',
               'opponent_team_abbreviation'] = 'GS'
        from app.cli.hoopr_import import _rows_from_player_box
        rows, _ = _rows_from_player_box(
            df, season='2025-26', season_type_code=2)
        self.assertEqual(len(rows), 4)
        abbrs = {r['team_abbr'] for r in rows} | {r['opp_abbr'] for r in rows}
        self.assertIn('GSW', abbrs)
        self.assertNotIn('GS', abbrs)

    def test_all_star_exhibition_rows_dropped_and_reported(self):
        rows = self._rows()
        self.assertNotIn('401777777', {r['game_id'] for r in rows})
        from app.cli.hoopr_import import _rows_from_player_box
        _, dropped = _rows_from_player_box(
            _player_box_df(), season='2025-26', season_type_code=2)
        self.assertEqual(dropped, {'STARS': 1, 'WORLD': 1})

    def test_maps_columns_and_skips_dnp_and_other_season_types(self):
        rows = self._rows()
        self.assertEqual(len(rows), 4)   # DNP + playoff + All-Star dropped
        lebron = next(r for r in rows if r['player_id'] == '1966')
        self.assertEqual(lebron['sport'], 'nba')
        self.assertEqual(lebron['player_name'], 'LeBron James')
        self.assertEqual(lebron['team_abbr'], 'LAL')
        self.assertEqual(lebron['opp_abbr'], 'BOS')
        self.assertEqual(lebron['game_id'], '401700001')
        self.assertEqual(lebron['game_date'], date(2025, 10, 21))
        self.assertEqual(lebron['season'], '2025-26')
        self.assertEqual(lebron['home_away'], 'HOME')
        self.assertEqual(lebron['win_loss'], 'W')
        self.assertTrue(lebron['starter'])
        self.assertEqual(lebron['stats']['pts'], 28.0)
        self.assertEqual(lebron['stats']['reb'], 7.0)
        self.assertEqual(lebron['stats']['ast'], 11.0)
        self.assertEqual(lebron['stats']['stl'], 1.0)
        self.assertEqual(lebron['stats']['blk'], 0.0)
        self.assertEqual(lebron['stats']['tov'], 3.0)
        self.assertEqual(lebron['stats']['fgm'], 10.0)
        self.assertEqual(lebron['stats']['fga'], 19.0)
        self.assertEqual(lebron['stats']['fg3m'], 2.0)
        self.assertEqual(lebron['stats']['fg3a'], 6.0)
        self.assertEqual(lebron['stats']['ftm'], 6.0)
        self.assertEqual(lebron['stats']['fta'], 7.0)
        self.assertEqual(lebron['stats']['minutes'], 36.0)
        self.assertEqual(lebron['stats']['plus_minus'], 12.0)
        tatum = next(r for r in rows if r['player_id'] == '4065648')
        self.assertEqual(tatum['home_away'], 'AWAY')
        self.assertEqual(tatum['win_loss'], 'L')
        self.assertEqual(tatum['stats']['plus_minus'], -12.0)
        white = next(r for r in rows if r['player_id'] == '3078576')
        self.assertFalse(white['starter'])

    def test_usage_pct_computed_from_team_totals(self):
        # LAL totals (played rows): min 66, fga 34, fta 12, tov 5
        # LeBron: ((19 + 0.44*7 + 3) * (66/5)) / (36 * (34 + 0.44*12 + 5))
        rows = self._rows()
        lebron = next(r for r in rows if r['player_id'] == '1966')
        expected = ((19 + 0.44 * 7 + 3) * (66 / 5)) / (36 * (34 + 0.44 * 12 + 5))
        self.assertAlmostEqual(lebron['stats']['usage_pct'], expected, places=6)
        # BOS totals: min 70, fga 36, fta 7, tov 3
        tatum = next(r for r in rows if r['player_id'] == '4065648')
        expected_t = ((24 + 0.44 * 5 + 2) * (70 / 5)) / (38 * (36 + 0.44 * 7 + 3))
        self.assertAlmostEqual(tatum['stats']['usage_pct'], expected_t, places=6)

    def test_playoffs_selected_by_season_type_code(self):
        rows = self._rows(season_type_code=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['game_id'], '401799999')

    def test_max_games_keeps_whole_games(self):
        df = _player_box_df()
        # add a second regular-season game, later date
        extra = df.iloc[0].copy()
        extra['game_id'] = 401700002
        extra['game_date'] = date(2025, 10, 23)
        df = pd.concat([df, extra.to_frame().T], ignore_index=True)
        from app.cli.hoopr_import import _rows_from_player_box
        rows, _ = _rows_from_player_box(
            df, season='2025-26', season_type_code=2, max_games=1)
        self.assertEqual({r['game_id'] for r in rows}, {'401700001'})
        self.assertEqual(len(rows), 4)   # all rows of the kept game


class TestImportCommand(BaseTestCase):

    def _run(self, args):
        runner = self.app.test_cli_runner()
        return runner.invoke(args=['import-hoopr-logs'] + args)

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_import_inserts_and_logs_success(self, mock_load):
        from app.models import HistoricalGameLog, JobLog
        mock_load.return_value = _player_box_df()
        result = self._run(['--sport', 'nba', '--seasons', '1'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(HistoricalGameLog.query.count(), 4)
            row = HistoricalGameLog.query.filter_by(player_id='1966').one()
            self.assertTrue(row.starter)        # born enriched
            self.assertIn('usage_pct', row.stats)
            job = JobLog.query.filter_by(job_name='import-hoopr-logs').one()
            self.assertEqual(job.status, 'success')

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_import_is_idempotent(self, mock_load):
        from app.models import HistoricalGameLog
        mock_load.return_value = _player_box_df()
        self._run(['--sport', 'nba', '--seasons', '1'])
        result = self._run(['--sport', 'nba', '--seasons', '1'])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            self.assertEqual(HistoricalGameLog.query.count(), 4)

    def test_non_nba_sport_rejected(self):
        result = self._run(['--sport', 'mlb', '--seasons', '1'])
        self.assertNotEqual(result.exit_code, 0)

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_load_failure_marks_job_failed_not_stuck_running(self, mock_load):
        from app.models import JobLog
        mock_load.side_effect = RuntimeError('github unreachable')
        result = self._run(['--sport', 'nba', '--seasons', '1'])
        self.assertEqual(result.exit_code, 0)   # reports, doesn't crash
        with self.app.app_context():
            job = JobLog.query.filter_by(job_name='import-hoopr-logs').one()
            self.assertEqual(job.status, 'failed')
            self.assertIn('github unreachable', job.message)
            self.assertIsNotNone(job.finished_at)

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_warns_when_season_mixes_nba_id_namespace(self, mock_load):
        from app import db
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(HistoricalGameLog(
                sport='nba', player_id='2544', player_name='LeBron James',
                game_id='0022500001', game_date=date(2025, 10, 21),
                season='2025-26', stats={'pts': 28.0}))
            db.session.commit()
        mock_load.return_value = _player_box_df()
        result = self._run(['--sport', 'nba', '--seasons', '1'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('WARNING', result.output)
        self.assertIn('stats.nba.com', result.output)


class TestImportCallable(BaseTestCase):

    @patch('app.cli.hoopr_import._load_player_box_df')
    def test_import_hoopr_seasons_returns_counts(self, mock_load):
        from app.cli.hoopr_import import import_hoopr_seasons
        mock_load.return_value = _player_box_df()
        with self.app.app_context():
            result = import_hoopr_seasons(seasons=1)
        self.assertEqual(result['inserted'], 4)
        self.assertEqual(result['errors'], [])
