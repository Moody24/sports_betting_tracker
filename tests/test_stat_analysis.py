"""Tests for the /nba/stat-analysis route and helpers."""

import json
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

from tests.helpers import BaseTestCase, make_user
from app import db
from app.models import PlayerGameLog
from app.routes.bet import _compute_hit_rates, _build_stat_context


class TestComputeHitRates(BaseTestCase):

    def _add_logs(self, player_name, values):
        """Insert PlayerGameLog rows with given pts values."""
        for i, val in enumerate(values):
            log = PlayerGameLog(
                player_id=f'test_{i}',
                player_name=player_name,
                game_date=date(2026, 1, i + 1),
                pts=float(val),
            )
            db.session.add(log)
        db.session.commit()

    def test_over_pct_correct(self):
        """3/5 games >= line (25) → 60% over."""
        with self.app.app_context():
            self._add_logs('Test Player', [28, 30, 22, 25, 20])
            result = _compute_hit_rates('Test Player', 'player_points', 25.0)
            self.assertEqual(result['over_pct'], 60)
            self.assertEqual(result['under_pct'], 40)
            self.assertEqual(result['sample'], 5)

    def test_no_logs_returns_safe_dict(self):
        with self.app.app_context():
            result = _compute_hit_rates('Nobody', 'player_points', 20.0)
            self.assertIsNone(result['over_pct'])
            self.assertIsNone(result['under_pct'])
            self.assertEqual(result['games'], [])
            self.assertEqual(result['sample'], 0)

    def test_combo_prop_returns_none_pct(self):
        """PRA has no mapped column → over_pct None."""
        with self.app.app_context():
            result = _compute_hit_rates('Test Player', 'player_points_rebounds_assists', 40.0)
            self.assertIsNone(result['over_pct'])
            self.assertIsNone(result['under_pct'])

    def test_games_list_capped_at_10(self):
        """games list in result is capped at 10 entries."""
        with self.app.app_context():
            self._add_logs('Big Log Player', [20] * 15)
            result = _compute_hit_rates('Big Log Player', 'player_points', 18.0, n=20)
            self.assertLessEqual(len(result['games']), 10)


class TestBuildStatContext(BaseTestCase):

    def test_blowout_risk_detected(self):
        """If moneyline_home >= 400 absolute → blowout_risk True."""
        with self.app.app_context():
            score = {'game_id': 'g1', 'player_team_abbr': 'LAL', 'prop_type': 'player_points', 'breakdown': {}}
            games_today = [{'espn_id': 'g1', 'moneyline_home': -450, 'moneyline_away': 370,
                            'over_under_line': 215.5, 'home': {'abbr': 'BOS'}, 'away': {'abbr': 'LAL'}}]
            ctx = _build_stat_context(score, games_today)
            self.assertTrue(ctx['blowout_risk'])

    def test_no_blowout_risk(self):
        """Balanced moneylines → blowout_risk False."""
        with self.app.app_context():
            score = {'game_id': 'g2', 'player_team_abbr': 'LAL', 'prop_type': 'player_points', 'breakdown': {}}
            games_today = [{'espn_id': 'g2', 'moneyline_home': -150, 'moneyline_away': 130,
                            'over_under_line': 220.0, 'home': {'abbr': 'BOS'}, 'away': {'abbr': 'LAL'}}]
            ctx = _build_stat_context(score, games_today)
            self.assertFalse(ctx['blowout_risk'])

    def test_missing_game_returns_safe_dict(self):
        """Score references unknown game_id → safe ctx with None values."""
        with self.app.app_context():
            score = {'game_id': 'missing', 'player_team_abbr': 'LAL', 'prop_type': 'player_points', 'breakdown': {}}
            ctx = _build_stat_context(score, [])
            self.assertIsNone(ctx['opp_def_rating'])
            self.assertIsNone(ctx['opp_stat_allowed'])


class TestStatAnalysisRoute(BaseTestCase):

    def setUp(self):
        super().setUp()
        from app.services.score_cache import invalidate_scores
        invalidate_scores()

    def _login(self):
        return self.register_and_login()

    def _mock_scores(self, overrides=None):
        base = {
            'player': 'Test Player',
            'prop_type': 'player_points',
            'line': 25.5,
            'projection': 28.0,
            'edge': 0.12,
            'confidence_tier': 'moderate',
            'win_probability': 0.55,
            'game_id': 'game1',
            'player_team_abbr': 'LAL',
            'recommended_odds': -110,
            'over_odds': -110,
            'breakdown': {'player_position': 'SF'},
            'context_notes': ['B2B'],
            'recommendation': 'Over',
        }
        if overrides:
            base.update(overrides)
        return [base]

    def _mock_games(self):
        return [{
            'espn_id': 'game1',
            'home': {'abbr': 'BOS', 'name': 'Celtics'},
            'away': {'abbr': 'LAL', 'name': 'Lakers'},
            'moneyline_home': -200,
            'moneyline_away': 170,
            'over_under_line': 220.0,
            'start_time': '2026-03-09T19:30:00Z',
        }]

    @patch('app.services.nba_service.get_todays_games')
    @patch('app.services.score_cache.get_todays_scores')
    def test_returns_200(self, mock_scores, mock_games):
        mock_scores.return_value = self._mock_scores()
        mock_games.return_value = self._mock_games()
        self._login()
        resp = self.client.get('/nba/stat-analysis')
        self.assertEqual(resp.status_code, 200)

    @patch('app.services.nba_service.get_todays_games')
    @patch('app.services.score_cache.get_todays_scores')
    def test_stat_filter_narrows_props(self, mock_scores, mock_games):
        scores = self._mock_scores() + self._mock_scores({'prop_type': 'player_rebounds', 'player': 'P2'})
        mock_scores.return_value = scores
        mock_games.return_value = self._mock_games()
        self._login()
        resp = self.client.get('/nba/stat-analysis?stat=player_rebounds')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn('P2', body)
        self.assertNotIn('Test Player', body)

    @patch('app.services.nba_service.get_todays_games')
    @patch('app.services.score_cache.get_todays_scores')
    def test_search_filter(self, mock_scores, mock_games):
        scores = self._mock_scores() + self._mock_scores({'player': 'Curry Test', 'game_id': 'game1'})
        mock_scores.return_value = scores
        mock_games.return_value = self._mock_games()
        self._login()
        resp = self.client.get('/nba/stat-analysis?q=curry')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn('Curry Test', body)
        self.assertNotIn('Test Player', body)

    @patch('app.services.nba_service.get_todays_games')
    @patch('app.services.score_cache.get_todays_scores')
    def test_indicator_strong(self, mock_scores, mock_games):
        mock_scores.return_value = self._mock_scores({'confidence_tier': 'strong', 'edge': 0.20})
        mock_games.return_value = self._mock_games()
        self._login()
        resp = self.client.get('/nba/stat-analysis')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'sa-ind-badge-strong', resp.data)

    @patch('app.services.nba_service.get_todays_games')
    @patch('app.services.score_cache.get_todays_scores')
    def test_indicator_avoid_on_low_wp(self, mock_scores, mock_games):
        """wp=0.35 overrides moderate confidence_tier → avoid."""
        mock_scores.return_value = self._mock_scores({'confidence_tier': 'moderate', 'win_probability': 0.35})
        mock_games.return_value = self._mock_games()
        self._login()
        resp = self.client.get('/nba/stat-analysis')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'sa-ind-badge-avoid', resp.data)

    @patch('app.services.nba_service.get_todays_games')
    @patch('app.services.score_cache.get_todays_scores')
    def test_matchup_grouping(self, mock_scores, mock_games):
        """Props with same game_id appear in the same matchup card."""
        mock_scores.return_value = self._mock_scores() + self._mock_scores({'player': 'Second Player'})
        mock_games.return_value = self._mock_games()
        self._login()
        resp = self.client.get('/nba/stat-analysis')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        # Both players from game1 should appear
        self.assertIn('Test Player', body)
        self.assertIn('Second Player', body)
        # Only one matchup card since both share game1
        self.assertEqual(body.count('matchup-card'), 1)

    def test_requires_login(self):
        resp = self.client.get('/nba/stat-analysis')
        self.assertIn(resp.status_code, (302, 401))


if __name__ == '__main__':
    unittest.main()
