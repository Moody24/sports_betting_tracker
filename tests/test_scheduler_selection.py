"""Pure selection tests for the daily auto-pick scheduler."""

import unittest
from unittest.mock import patch

from app.services.scheduler import (
    _build_parlay_groups,
    _paper_play_qualifies,
)


class _FakeBet:
    next_id = 0

    @classmethod
    def generate_parlay_id(cls):
        cls.next_id += 1
        return f'parlay-{cls.next_id}'


class SchedulerSelectionTests(unittest.TestCase):
    def test_parlay_groups_do_not_reuse_players_or_games(self):
        plays = [
            {'player': f'Player {index}', 'game_id': f'game-{index}', 'edge': 0.12}
            for index in range(1, 7)
        ]
        with patch('app.services.scheduler.AUTO_PICK_MIN_EDGE_2LEG', 0.05), \
                patch('app.services.scheduler.AUTO_PICK_MIN_EDGE_3LEG', 0.08), \
                patch('app.services.scheduler.AUTO_PICK_MAX_TOTAL', 6):
            groups = _build_parlay_groups(plays, [], _FakeBet)

        flattened = [play for _, group in groups for play in group]
        self.assertEqual(len(flattened), 6)
        self.assertEqual(len({play['player'] for play in flattened}), 6)
        self.assertEqual(len({play['game_id'] for play in flattened}), 6)

    def test_paper_filter_rejects_existing_market_and_player(self):
        play = {
            'player': 'LeBron James',
            'prop_type': 'player_points',
            'line': 27.5,
            'recommended_side': 'over',
            'game_id': 'game-1',
            'edge': 0.10,
            'confidence_tier': 'moderate',
        }
        kwargs = {
            'market_keys': set(),
            'paper_players': set(),
            'min_edge': 0.08,
            'max_edge': 0.15,
            'min_tier_rank': 2,
            'tier_rank': {'no_edge': 0, 'slight': 1, 'moderate': 2, 'strong': 3},
        }
        self.assertTrue(_paper_play_qualifies(play, **kwargs))
        kwargs['paper_players'].add('LeBron James')
        self.assertFalse(_paper_play_qualifies(play, **kwargs))


if __name__ == '__main__':
    unittest.main()
