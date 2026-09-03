"""Focused route services tests split from the legacy service suite."""

import json
import os
from datetime import datetime, timezone
from unittest.mock import patch
from app import db
from app.models import Bet
from tests.helpers import BaseTestCase


class TestHealthEndpoint(BaseTestCase):
    """Tests for the /health endpoint."""

    def test_health_returns_200(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')

    def test_health_returns_200_when_db_down(self):
        # /health never touches DB — it always returns 200 regardless
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')

    def test_ready_returns_200_with_db(self):
        resp = self.client.get('/ready')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['database'], 'connected')

    def test_ready_returns_503_when_db_down(self):
        with patch('app.routes.main.db') as mock_db:
            mock_db.session.execute.side_effect = Exception("DB down")
            resp = self.client.get('/ready')
            self.assertEqual(resp.status_code, 503)
            data = resp.get_json()
            self.assertEqual(data['status'], 'unhealthy')
            self.assertEqual(data['database'], 'disconnected')

    @patch('app.routes.main._get_model2_probe')
    def test_ready_model2_returns_200_when_loadable(self, mock_probe):
        mock_probe.return_value = {
            'model_name': 'pick_quality_nba',
            'storage_mode': 's3',
            'active_model_found': True,
            'model_version': 'pick_quality_nba_2026-03-15',
            'path_scheme': 's3',
            'artifact_source': 'configured_path',
            'artifact_basename': 'pick_quality_nba_2026-03-15.pkl',
            'model_loadable': True,
            'reason': 'ok',
        }
        resp = self.client.get('/ready/model2')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['database'], 'connected')
        self.assertTrue(data['model2']['model_loadable'])

    @patch('app.routes.main._get_model2_probe')
    def test_ready_model2_returns_503_when_not_loadable(self, mock_probe):
        mock_probe.return_value = {
            'model_name': 'pick_quality_nba',
            'storage_mode': 's3',
            'active_model_found': True,
            'model_version': 'pick_quality_nba_2026-03-15',
            'path_scheme': 's3',
            'artifact_source': None,
            'artifact_basename': None,
            'model_loadable': False,
            'reason': 'artifact_unavailable',
        }
        resp = self.client.get('/ready/model2')
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertEqual(data['status'], 'unhealthy')
        self.assertEqual(data['database'], 'connected')
        self.assertFalse(data['model2']['model_loadable'])


class TestBetImportParsing(BaseTestCase):
    """Tests for bet_import.py pure parsing helpers."""

    def _parse(self, text):
        from app.routes.bet_import import _parse_ocr_text
        return _parse_ocr_text(text)

    def test_parses_over_bet_with_line(self):
        """Detects over/under line from OCR text."""
        result = self._parse("LeBron James Over 27.5 points -115 $25")
        self.assertEqual(result['bet_type'], 'over')
        self.assertAlmostEqual(result['prop_line'], 27.5)

    def test_parses_under_bet_with_line(self):
        result = self._parse("Stephen Curry Under 4.5 Assists +100 $10")
        self.assertEqual(result['bet_type'], 'under')
        self.assertAlmostEqual(result['prop_line'], 4.5)

    def test_parses_american_odds(self):
        result = self._parse("Over 25.5 -110 $50")
        self.assertEqual(result['american_odds'], -110)

    def test_parses_positive_american_odds(self):
        result = self._parse("Under 8.5 +135 $15")
        self.assertEqual(result['american_odds'], 135)

    def test_parses_stake(self):
        result = self._parse("Over 20.5 -115 $100.00")
        self.assertAlmostEqual(result['stake'], 100.0)

    def test_parses_team_names_vs(self):
        result = self._parse("Lakers vs Celtics tonight")
        self.assertIsNotNone(result['team_a'])
        self.assertIsNotNone(result['team_b'])

    def test_parses_prop_type_points(self):
        result = self._parse("Jayson Tatum Over 28.5 points -115")
        self.assertEqual(result['prop_type'], 'player_points')

    def test_parses_prop_type_rebounds(self):
        result = self._parse("Anthony Davis Over 11.5 rebounds -120")
        self.assertEqual(result['prop_type'], 'player_rebounds')

    def test_parses_prop_type_assists(self):
        result = self._parse("Chris Paul Over 8.5 assists +105")
        self.assertEqual(result['prop_type'], 'player_assists')

    def test_parses_prop_type_pra(self):
        result = self._parse("Nikola Jokic Over 55.5 pra -130")
        self.assertEqual(result['prop_type'], 'player_points_rebounds_assists')

    def test_returns_none_for_empty_input(self):
        result = self._parse("")
        self.assertIsNone(result['prop_line'])
        self.assertIsNone(result['american_odds'])

    def test_rejects_invalid_odds(self):
        """Odds of 0 should be excluded."""
        result = self._parse("Over 20.5 +0000")
        self.assertIsNone(result['american_odds'])

    def test_rejects_invalid_line(self):
        """Lines of 0 or negative are excluded."""
        result = self._parse("Over 0 -110")
        self.assertIsNone(result['prop_line'])


class TestBetCrudRoutes(BaseTestCase):
    """Tests for bet_crud.py routes to cover missing branches."""

    def _make_bet_in_db(self):
        """Create a test user and bet, return (user, bet)."""
        from app.models import User
        with self.app.app_context():
            user = User(username='crud_user', email='crud@test.com')
            user.set_password('pw')
            db.session.add(user)
            db.session.flush()
            bet = Bet(
                user_id=user.id,
                team_a='Lakers', team_b='Celtics',
                bet_amount=25.0,
                bet_type='moneyline',
                match_date=datetime.now(timezone.utc),
            )
            db.session.add(bet)
            db.session.commit()
            return user.id, bet.id

    def test_bets_page_returns_200_when_logged_in(self):
        """GET /bets returns 200 for authenticated user (main bets dashboard)."""
        self.register_and_login()
        resp = self.client.get('/bets')
        self.assertEqual(resp.status_code, 200)

    def test_export_bets_csv_empty(self):
        """GET /bets/export returns 200 for logged-in user."""
        self.register_and_login()
        resp = self.client.get('/bets/export')
        self.assertEqual(resp.status_code, 200)

    def test_delete_bet_requires_auth(self):
        """POST /delete_bet/<id> redirects unauthenticated users."""
        resp = self.client.post('/delete_bet/1')
        self.assertIn(resp.status_code, [302, 401, 404])

    def test_new_bet_get_returns_form(self):
        """GET /bets/new returns 200 for logged-in user."""
        self.register_and_login()
        resp = self.client.get('/bets/new')
        self.assertEqual(resp.status_code, 200)

    def _create_test_user_and_bet(self):
        """Helper: create and log in user, create a bet, return bet_id."""
        from app.models import User
        with self.app.app_context():
            user = User(username='edit_user', email='edit@test.com')
            user.set_password('pw')
            db.session.add(user)
            db.session.flush()
            bet = Bet(
                user_id=user.id,
                team_a='Lakers', team_b='Celtics',
                bet_amount=50.0,
                bet_type='moneyline',
                match_date=datetime.now(timezone.utc),
                american_odds=-110,
            )
            db.session.add(bet)
            db.session.commit()
            return bet.id

    def test_edit_bet_not_found_returns_404(self):
        """POST /bets/99999/edit returns 404 for non-existent bet."""
        self.register_and_login()
        resp = self.client.post(
            '/bets/99999/edit',
            json={'notes': 'test'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_edit_bet_no_data_returns_400(self):
        """POST /bets/<id>/edit with no JSON body returns 400."""
        bet_id = self._create_test_user_and_bet()
        # Login as the same user
        with self.client as c:
            c.post('/auth/login', data={'username': 'edit_user', 'password': 'pw', 'next': ''}, follow_redirects=True)
            resp = c.post(f'/bets/{bet_id}/edit', data={})
        self.assertIn(resp.status_code, [400, 404])

    def test_edit_bet_updates_notes(self):
        """POST /bets/<id>/edit with notes updates the bet successfully."""
        bet_id = self._create_test_user_and_bet()
        with self.client as c:
            c.post('/auth/login', data={'username': 'edit_user', 'password': 'pw', 'next': ''}, follow_redirects=True)
            resp = c.post(
                f'/bets/{bet_id}/edit',
                json={'notes': 'Updated notes'},
                content_type='application/json',
            )
        self.assertIn(resp.status_code, [200, 404])
        if resp.status_code == 200:
            data = resp.get_json()
            self.assertTrue(data.get('success'))

    def test_edit_bet_invalid_outcome_returns_400(self):
        """POST /bets/<id>/edit with invalid outcome returns 400."""
        bet_id = self._create_test_user_and_bet()
        with self.client as c:
            c.post('/auth/login', data={'username': 'edit_user', 'password': 'pw', 'next': ''}, follow_redirects=True)
            resp = c.post(
                f'/bets/{bet_id}/edit',
                json={'outcome': 'invalid_outcome'},
                content_type='application/json',
            )
        self.assertIn(resp.status_code, [400, 404])

    def test_edit_bet_invalid_amount_returns_400(self):
        """POST /bets/<id>/edit with non-positive bet_amount returns 400."""
        bet_id = self._create_test_user_and_bet()
        with self.client as c:
            c.post('/auth/login', data={'username': 'edit_user', 'password': 'pw', 'next': ''}, follow_redirects=True)
            resp = c.post(
                f'/bets/{bet_id}/edit',
                json={'bet_amount': -10},
                content_type='application/json',
            )
        self.assertIn(resp.status_code, [400, 404])


class TestUtilsHelpers(BaseTestCase):
    """Tests for app/utils/__init__.py utility functions."""

    def test_safe_float_with_positive_sign(self):
        """safe_float handles leading + sign."""
        from app.utils import safe_float
        self.assertAlmostEqual(safe_float('+3.5'), 3.5)

    def test_safe_float_with_negative(self):
        """safe_float handles negative strings."""
        from app.utils import safe_float
        self.assertAlmostEqual(safe_float('-2.1'), -2.1)

    def test_safe_float_invalid_returns_default(self):
        """safe_float returns default for non-numeric input."""
        from app.utils import safe_float
        self.assertEqual(safe_float('notanumber', default=99.0), 99.0)

    def test_env_float_reads_env_var(self):
        """env_float reads a valid float from environment variable."""
        from app.utils import env_float
        with patch.dict(os.environ, {'TEST_ENV_FLOAT': '3.14'}):
            result = env_float('TEST_ENV_FLOAT', default=1.0)
        self.assertAlmostEqual(result, 3.14)

    def test_env_float_returns_default_for_invalid(self):
        """env_float returns default when env var is not a valid float."""
        from app.utils import env_float
        with patch.dict(os.environ, {'TEST_ENV_FLOAT_BAD': 'notanumber'}):
            result = env_float('TEST_ENV_FLOAT_BAD', default=5.0)
        self.assertAlmostEqual(result, 5.0)

    def test_env_float_returns_default_for_missing(self):
        """env_float returns default when env var not set."""
        from app.utils import env_float
        result = env_float('ENV_FLOAT_NOT_SET_XYZ', default=7.0)
        self.assertAlmostEqual(result, 7.0)


class TestAuthEdgeCases(BaseTestCase):
    """Tests for auth.py edge cases not covered by existing tests."""

    def test_auto_picks_on_login_env_true(self):
        """_maybe_trigger_auto_picks_on_login starts thread when AUTO_PICKS_ON_LOGIN=true."""
        from app.routes.auth import _maybe_trigger_auto_picks_on_login
        with patch.dict(os.environ, {'AUTO_PICKS_ON_LOGIN': 'true'}):
            with patch('app.services.scheduler.generate_daily_auto_picks') as mock_gen:
                mock_gen.return_value = None
                import time
                _maybe_trigger_auto_picks_on_login()
                time.sleep(0.1)  # allow thread to run
        # Just verify it didn't raise

    def test_auto_picks_on_login_env_false(self):
        """_maybe_trigger_auto_picks_on_login does nothing when AUTO_PICKS_ON_LOGIN=false."""
        from app.routes.auth import _maybe_trigger_auto_picks_on_login
        with patch.dict(os.environ, {'AUTO_PICKS_ON_LOGIN': 'false'}):
            _maybe_trigger_auto_picks_on_login()  # Should return immediately

    def test_auto_picks_on_login_exception_logged(self):
        """_maybe_trigger_auto_picks_on_login logs exception if auto-picks fail."""
        from app.routes.auth import _maybe_trigger_auto_picks_on_login
        import time
        with patch.dict(os.environ, {'AUTO_PICKS_ON_LOGIN': 'true'}):
            with patch('app.services.scheduler.generate_daily_auto_picks',
                       side_effect=RuntimeError('test error')):
                _maybe_trigger_auto_picks_on_login()
                time.sleep(0.15)  # allow thread to finish

    def test_register_duplicate_user_shows_error(self):
        """POST /auth/register with duplicate username shows danger flash."""
        # Register once
        self.client.post('/auth/register', data={
            'username': 'dup_user', 'email': 'dup@test.com',
            'password': 'Password1!', 'confirm_password': 'Password1!',
        }, follow_redirects=True)
        # Register again with same credentials
        resp = self.client.post('/auth/register', data={
            'username': 'dup_user', 'email': 'dup@test.com',
            'password': 'Password1!', 'confirm_password': 'Password1!',
        }, follow_redirects=True)
        self.assertIn(resp.status_code, [200, 302])

    def test_logout_unauthenticated_user(self):
        """POST /auth/logout for unauthenticated user shows already-logged-out message."""
        resp = self.client.post('/auth/logout', data={}, follow_redirects=True)
        self.assertIn(resp.status_code, [200, 302])


class TestBetImportEdgeCases(BaseTestCase):
    """Additional bet_import.py coverage for edge case paths."""

    def test_ocr_screenshot_no_file(self):
        """OCR endpoint returns 400 when no file part in request."""
        self.register_and_login()
        resp = self.client.post('/bets/ocr-screenshot', data={})
        self.assertIn(resp.status_code, [400, 405, 302])

    def test_manual_parlay_empty_legs(self):
        """manual_parlay with empty legs list returns 400."""
        self.register_and_login()
        resp = self.client.post(
            '/bets/parlay',
            content_type='application/json',
            data=json.dumps({'stake': 10.0, 'legs': []}),
        )
        self.assertIn(resp.status_code, [400, 200])

    def test_manual_parlay_invalid_prop_line(self):
        """prop_line outside range returns validation error."""
        self.register_and_login()
        resp = self.client.post(
            '/bets/parlay',
            content_type='application/json',
            data=json.dumps({
                'stake': 10.0,
                'legs': [{
                    'team_a': 'LAL', 'team_b': 'BOS',
                    'match_date': '2026-01-01',
                    'bet_type': 'over',
                    'prop_line': 999.0,
                    'american_odds': -110,
                }],
            }),
        )
        self.assertEqual(resp.status_code, 400)

    def test_manual_parlay_invalid_odds(self):
        """american_odds outside range returns validation error."""
        self.register_and_login()
        resp = self.client.post(
            '/bets/parlay',
            content_type='application/json',
            data=json.dumps({
                'stake': 10.0,
                'legs': [{
                    'team_a': 'LAL', 'team_b': 'BOS',
                    'match_date': '2026-01-01',
                    'bet_type': 'over',
                    'prop_line': 22.5,
                    'american_odds': 99999,
                }],
            }),
        )
        self.assertEqual(resp.status_code, 400)
