"""Point-in-time opponent context for historical model rows."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.services.historical_defense import rolling_defense_by_game
from app.services.ml_model import _build_training_rows


def game_logs(count=12):
    rows = []
    for game in range(count):
        game_date = date(2025, 10, 20) + timedelta(days=game)
        for team, opponent, points in (
            ('BOS', 'NYK', 160 if game == 10 else 96),
            ('NYK', 'BOS', 88),
        ):
            for player in range(8):
                rows.append(SimpleNamespace(
                    player_id=f'{team}-{player}', player_name=f'{team} {player}',
                    team_abbr=team, matchup=f'{team} vs. {opponent}',
                    game_date=game_date, home_away='home',
                    _historical_game_id=str(game),
                    pts=points / 8, reb=5, ast=3, fg3m=1,
                    stl=1, blk=1, tov=1, fga=10, fta=2,
                    fg3a=3, fgm=4, ftm=2, minutes=30,
                ))
    return rows


class HistoricalDefenseTrainingTests(unittest.TestCase):
    def test_profiles_use_only_completed_prior_games(self):
        profiles = rolling_defense_by_game(game_logs())
        first = date(2025, 10, 20)
        self.assertNotIn((first + timedelta(days=4), 'NYK'), profiles)
        self.assertEqual(profiles[(first + timedelta(days=5), 'NYK')]['opp_pts_pg'], 96)
        self.assertEqual(profiles[(first + timedelta(days=10), 'NYK')]['opp_pts_pg'], 96)
        self.assertEqual(profiles[(first + timedelta(days=11), 'NYK')]['opp_pts_pg'], 102.4)

    @patch('app.services.historical_training_source.load_historical_training_logs')
    @patch('app.services.ml_model.build_defense_lookup')
    def test_model_rows_do_not_read_latest_snapshot(self, live_defense, load_logs):
        load_logs.return_value = game_logs()
        live_defense.side_effect = AssertionError('latest defense leaks into history')

        rows = _build_training_rows('player_points', min_train_samples=1)

        row = next(row for row in rows if row[0] == date(2025, 10, 30)
                   and row[1] == 'BOS-0')
        self.assertEqual(row[2]['opp_stat_allowed'], 96)
        self.assertGreater(row[2]['opp_pace'], 0)
        live_defense.assert_not_called()


if __name__ == '__main__':
    unittest.main()
