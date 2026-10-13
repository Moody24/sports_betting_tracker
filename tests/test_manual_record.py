"""Manual recording preserves user prices and provenance without provider data."""

from app.enums import BetSource
from app.models import Bet
from tests.helpers import BaseTestCase


class TestManualRecord(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.register_and_login()

    def payload(self):
        return {
            'stake': 20, 'is_parlay': False,
            'legs': [{
                'team_a': 'Away', 'team_b': 'Home', 'match_date': '2026-10-20',
                'game_id': '', 'bet_type': 'moneyline', 'picked_team': 'Home',
                'american_odds': '135.0', 'manual_entry': True,
            }],
        }

    def test_manual_record_preserves_source_and_recorded_price(self):
        response = self.client.post('/nba/place-bets', json=self.payload())
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            bet = Bet.query.one()
            self.assertEqual(bet.source, BetSource.MANUAL.value)
            self.assertEqual(bet.american_odds, 135)
            self.assertEqual(bet.bet_amount, 20)
            self.assertIsNone(bet.external_game_id)

    def test_manual_flag_must_be_boolean_before_writing(self):
        for value in ('true', 1, [], {}):
            payload = self.payload()
            payload['legs'][0]['manual_entry'] = value
            with self.subTest(value=value):
                response = self.client.post('/nba/place-bets', json=payload)
                self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(Bet.query.count(), 0)
