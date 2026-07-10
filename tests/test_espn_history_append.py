"""Tests for ESPN summary → HistoricalGameLog append."""

from unittest.mock import patch

from tests.helpers import BaseTestCase


def _summary_json():
    """Minimal 2-team, 2+1-player ESPN summary payload."""
    labels = ['MIN', 'FG', '3PT', 'FT', 'OREB', 'DREB', 'REB', 'AST',
              'STL', 'BLK', 'TO', 'PF', '+/-', 'PTS']

    def athlete(pid, name, starter, stats):
        return {'athlete': {'id': pid, 'displayName': name},
                'starter': starter, 'didNotPlay': not stats, 'stats': stats}

    return {'boxscore': {'players': [
        {'team': {'abbreviation': 'LAL'}, 'statistics': [{
            'labels': labels,
            'athletes': [
                athlete(1966, 'LeBron James', True,
                        ['36', '10-19', '2-6', '6-7', '1', '6', '7', '11',
                         '1', '0', '3', '2', '+12', '28']),
                athlete(6583, 'Anthony Davis', True,
                        ['30', '9-15', '0-1', '4-5', '3', '9', '12', '3',
                         '2', '3', '2', '3', '+8', '22']),
                athlete(999, 'Bench Guy', False, []),   # DNP
            ]}]},
        {'team': {'abbreviation': 'GS'}, 'statistics': [{   # ESPN alias
            'labels': labels,
            'athletes': [
                athlete(3975, 'Stephen Curry', True,
                        ['38', '12-24', '4-11', '5-5', '0', '4', '4', '5',
                         '2', '1', '2', '1', '-12', '33']),
            ]}]},
    ]}}


def _scoreboard_game(status='STATUS_FINAL'):
    return {
        'espn_id': '401800123',
        'home': {'abbr': 'LAL', 'score': 120}, 'away': {'abbr': 'GS', 'score': 110},
        'start_time': '2026-11-05T02:30Z', 'status': status,
    }


class TestAppendFinalGame(BaseTestCase):

    @patch('app.services.espn_history_append._fetch_summary')
    def test_appends_rows_with_mapped_fields(self, mock_fetch):
        from app.models import HistoricalGameLog
        from app.services.espn_history_append import append_final_game
        mock_fetch.return_value = _summary_json()
        with self.app.app_context():
            n = append_final_game(_scoreboard_game())
            self.assertEqual(n, 3)                      # DNP skipped
            lebron = HistoricalGameLog.query.filter_by(player_id='1966').one()
            self.assertEqual(lebron.sport, 'nba')
            self.assertEqual(lebron.game_id, '401800123')
            self.assertEqual(lebron.team_abbr, 'LAL')
            self.assertEqual(lebron.opp_abbr, 'GSW')     # alias normalized
            self.assertEqual(lebron.home_away, 'HOME')
            self.assertEqual(lebron.win_loss, 'W')       # 120 > 110
            self.assertEqual(lebron.season, '2026-27')   # from start_time date
            self.assertTrue(lebron.starter)
            s = lebron.stats
            self.assertEqual((s['pts'], s['reb'], s['ast']), (28.0, 7.0, 11.0))
            self.assertEqual((s['fgm'], s['fga'], s['fg3m'], s['fg3a']),
                             (10.0, 19.0, 2.0, 6.0))
            self.assertEqual((s['ftm'], s['fta'], s['stl'], s['blk'], s['tov']),
                             (6.0, 7.0, 1.0, 0.0, 3.0))
            self.assertEqual(s['minutes'], 36.0)
            self.assertEqual(s['plus_minus'], 12.0)
            # usage vs hand-computed LAL totals: min66 fga34 fta12 tov5
            expected = ((19 + 0.44 * 7 + 3) * (66 / 5)) / (36 * (34 + 0.44 * 12 + 5))
            self.assertAlmostEqual(s['usage_pct'], expected, places=6)
            curry = HistoricalGameLog.query.filter_by(player_id='3975').one()
            self.assertEqual(curry.team_abbr, 'GSW')
            self.assertEqual(curry.win_loss, 'L')
            self.assertEqual(curry.home_away, 'AWAY')

    @patch('app.services.espn_history_append._fetch_summary')
    def test_no_refetch_guard_skips_existing_game(self, mock_fetch):
        from app.services.espn_history_append import (
            append_final_game, history_rows_exist)
        mock_fetch.return_value = _summary_json()
        with self.app.app_context():
            self.assertFalse(history_rows_exist('401800123'))
            append_final_game(_scoreboard_game())
            self.assertTrue(history_rows_exist('401800123'))
            mock_fetch.reset_mock()
            n = append_final_game(_scoreboard_game())
            self.assertEqual(n, 0)
            mock_fetch.assert_not_called()               # guard fired BEFORE fetch

    @patch('app.services.espn_history_append._fetch_summary')
    def test_non_nba_team_skipped_entirely(self, mock_fetch):
        from app.services.espn_history_append import append_final_game
        payload = _summary_json()
        payload['boxscore']['players'][0]['team']['abbreviation'] = 'STARS'
        mock_fetch.return_value = payload
        game = _scoreboard_game()
        game['home']['abbr'] = 'STARS'
        with self.app.app_context():
            self.assertEqual(append_final_game(game), 0)

    @patch('app.services.espn_history_append._fetch_summary')
    def test_fetch_failure_returns_zero_not_raise(self, mock_fetch):
        from app.services.espn_history_append import append_final_game
        mock_fetch.side_effect = RuntimeError('espn down')
        with self.app.app_context():
            self.assertEqual(append_final_game(_scoreboard_game()), 0)
