"""Only verified active games may begin a guided prop record."""

from datetime import datetime, timezone
from unittest import TestCase

from app.services.game_picker import game_picker_options


class GamePickerTests(TestCase):
    now = datetime(2026, 10, 20, 23, tzinfo=timezone.utc)

    def game(self, **changes):
        game = {'espn_id': '123', 'start_time': '2026-10-21T01:00:00Z',
                'status': 'STATUS_SCHEDULED', 'home': {'name': 'Home'},
                'away': {'name': 'Away'}, 'odds_event_id': 'quote-123'}
        game.update(changes)
        return game

    def test_final_unknown_invalid_date_and_missing_identity_are_excluded(self):
        invalid = [self.game(status='STATUS_FINAL'), self.game(status='UNKNOWN'),
                   self.game(start_time='2026-10-20T20:00:00'),
                   self.game(start_time='2026-10-19T01:00:00Z'),
                   self.game(espn_id='', odds_event_id='')]
        self.assertEqual(game_picker_options(invalid, [], {}, now=self.now), [])

    def test_active_events_keep_et_date_and_provider_identity(self):
        games = game_picker_options([self.game(status='STATUS_IN_PROGRESS')], [], {}, now=self.now)
        self.assertEqual(games[0]['status'], 'in_progress')
        self.assertEqual(games[0]['match_date'], '2026-10-20')
        self.assertEqual(games[0]['selection_key'], 'espn:123')
        odds_only = game_picker_options([self.game(espn_id='')], [], {}, now=self.now)
        self.assertEqual(odds_only[0]['game_id'], '')
        self.assertEqual(odds_only[0]['selection_key'], 'odds_api:quote-123')

    def test_free_games_never_invent_market_linkage_and_stale_games_are_hidden(self):
        feed = {'state': 'current', 'match_date': '2026-10-20', 'games': [{
            'source_event_id': 'balldontlie:123', 'status': 'scheduled',
            'match_date': '2026-10-20', 'start_time': '2026-10-21T01:00:00Z',
            'home': {'name': 'Home'}, 'away': {'name': 'Away'},
        }]}
        result = game_picker_options([], [], feed, now=self.now)
        self.assertEqual(result[0]['selection_key'], 'balldontlie:123')
        self.assertEqual(result[0]['game_id'], '')
        self.assertEqual(result[0]['odds_event_id'], '')
        self.assertEqual(result[0]['market_state'], 'unavailable')
        self.assertIsNone(result[0]['moneyline_home'])
        feed['state'] = 'stale'
        self.assertEqual(game_picker_options([], [], feed, now=self.now), [])

    def test_duplicate_provider_events_are_not_repeated(self):
        self.assertEqual(len(game_picker_options([self.game(), self.game()], [], {}, now=self.now)), 1)
