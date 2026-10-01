"""Guard the historical matchup screen against same-game leakage."""

import unittest
from datetime import date, timedelta

import pandas as pd

from scripts.matchup_pattern_research import (
    compare_arms, prepare_player_games, prepare_team_games,
)


class MatchupPatternResearchTests(unittest.TestCase):
    def test_group_effect_is_not_mistaken_for_a_same_player_effect(self):
        rows = pd.DataFrame([
            {
                'game_id': str(game), 'season': '2025-26',
                'player_id': f'{arm}-{player}', 'prior_pts': 10,
                'pts_residual': 1 if arm == 'high' else 0,
                'pts_above_baseline': arm == 'high',
                'pts_below_baseline': False,
                'arm': arm,
            }
            for game in range(40)
            for arm in ('high', 'low')
            for player in range(3)
        ])
        result = compare_arms(
            rows, 'pts', rows['arm'] == 'high', rows['arm'] == 'low',
            iterations=100,
        )
        self.assertEqual(result['effect'], 1)
        self.assertEqual(result['paired_players'], 0)
        self.assertIsNone(result['paired_effect'])

    def test_player_and_opponent_profiles_use_only_prior_games(self):
        rows = []
        for game_index in range(12):
            played_at = date(2025, 10, 22) + timedelta(days=game_index * 2)
            for team, opponent, home in (
                ('BOS', 'NYK', 'HOME'), ('NYK', 'BOS', 'AWAY'),
            ):
                for player_index in range(8):
                    rows.append({
                        'game_id': str(game_index), 'game_date': played_at,
                        'season': '2025-26', 'team_abbr': team,
                        'opp_abbr': opponent, 'home_away': home,
                        'player_id': f'{team}-{player_index}',
                        'player_name': f'{team} Player {player_index}',
                        'team_score': 100 if team == 'BOS' else 95,
                        'opp_score': 95 if team == 'BOS' else 100,
                        'pts': 40 if game_index == 10 and player_index == 0
                        else 10,
                        'reb': 5, 'ast': 3, 'fg3m': 1,
                        'fg3a': 20 if team == 'BOS' and game_index == 10
                        else 2,
                        'fga': 10, 'fta': 2, 'stl': 1, 'blk': 1,
                        'tov': 1, 'minutes': 30,
                    })
        frame = pd.DataFrame(rows)
        teams = prepare_team_games(frame)
        games = prepare_player_games(frame, teams, pd.DataFrame())

        target = games[(games['game_id'] == '10')
                       & (games['player_id'] == 'BOS-0')].iloc[0]
        self.assertEqual(target['prior_pts'], 10)
        self.assertEqual(target['pts_residual'], 30)
        self.assertEqual(target['minutes_residual'], 0)
        self.assertEqual(target['prior_player_meetings'], 10)
        self.assertAlmostEqual(target['def_three_rate_allowed'], 0.2)
        self.assertEqual(target['rest_days'], 1)

        defense = teams[(teams['game_id'] == '10')
                        & (teams['team_abbr'] == 'NYK')].iloc[0]
        self.assertAlmostEqual(defense['prior_three_rate_allowed'], 0.2)
        self.assertGreater(defense['three_rate_allowed'], 0.2)


if __name__ == '__main__':
    unittest.main()
