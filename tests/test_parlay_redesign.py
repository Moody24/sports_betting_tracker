"""Tests for the parlay builder redesign: OddsSnapshot, multi-book odds, movement, scheduler job."""

import json
from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from app import db
from app.models import OddsSnapshot, Bet
from tests.helpers import BaseTestCase


class TestOddsSnapshotModel(BaseTestCase):
    """OddsSnapshot model creation + daily query."""

    def test_create_odds_snapshot(self):
        with self.app.app_context():
            today = date.today()
            snap = OddsSnapshot(
                game_id='abc123',
                game_date=today,
                player_name='LeBron James',
                market='player_points',
                bookmaker='fanduel',
                line=25.5,
                over_odds=-108,
                under_odds=-112,
            )
            db.session.add(snap)
            db.session.commit()

            loaded = OddsSnapshot.query.filter_by(game_id='abc123').first()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.player_name, 'LeBron James')
            self.assertEqual(loaded.market, 'player_points')
            self.assertEqual(loaded.bookmaker, 'fanduel')
            self.assertAlmostEqual(loaded.line, 25.5)
            self.assertEqual(loaded.over_odds, -108)
            self.assertEqual(loaded.under_odds, -112)

    def test_daily_query_by_game_date(self):
        with self.app.app_context():
            today = date.today()
            yesterday = today - timedelta(days=1)

            snap_today = OddsSnapshot(
                game_id='g1', game_date=today, player_name='Player A',
                market='player_points', bookmaker='fanduel', line=20.0,
                over_odds=-110, under_odds=-110,
            )
            snap_yesterday = OddsSnapshot(
                game_id='g1', game_date=yesterday, player_name='Player A',
                market='player_points', bookmaker='fanduel', line=19.5,
                over_odds=-110, under_odds=-110,
            )
            db.session.add_all([snap_today, snap_yesterday])
            db.session.commit()

            results = OddsSnapshot.query.filter_by(game_date=today).all()
            self.assertEqual(len(results), 1)
            self.assertAlmostEqual(results[0].line, 20.0)

    def test_repr(self):
        with self.app.app_context():
            snap = OddsSnapshot(
                game_id='x', game_date=date.today(), player_name='Steph Curry',
                market='player_points', bookmaker='draftkings', line=30.5,
                over_odds=-115, under_odds=-105,
            )
            self.assertIn('Steph Curry', repr(snap))
            self.assertIn('draftkings', repr(snap))


class TestMultiBookFetch(BaseTestCase):
    """fetch_player_props_for_event returns books dict and best-book fields."""

    def test_returns_books_dict_and_best_fields(self):
        from app.services.nba_service import fetch_player_props_for_event

        mock_response = {
            'bookmakers': [
                {
                    'key': 'fanduel',
                    'markets': [
                        {
                            'key': 'player_points',
                            'outcomes': [
                                {'description': 'LeBron James', 'name': 'Over', 'price': -108, 'point': 25.5},
                                {'description': 'LeBron James', 'name': 'Under', 'price': -112, 'point': 25.5},
                            ],
                        }
                    ],
                },
                {
                    'key': 'draftkings',
                    'markets': [
                        {
                            'key': 'player_points',
                            'outcomes': [
                                {'description': 'LeBron James', 'name': 'Over', 'price': -110, 'point': 25.5},
                                {'description': 'LeBron James', 'name': 'Under', 'price': -110, 'point': 25.5},
                            ],
                        }
                    ],
                },
            ]
        }

        with self.app.app_context():
            with patch('app.services.nba_service._get_odds_api_key', return_value='test_key'), \
                 patch('requests.get') as mock_get:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status.return_value = None
                mock_get.return_value = mock_resp

                props = fetch_player_props_for_event('event123')

        self.assertIn('player_points', props)
        entries = props['player_points']
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry['player'], 'LeBron James')
        self.assertAlmostEqual(entry['line'], 25.5)
        # books dict should have both bookmakers
        self.assertIn('fanduel', entry['books'])
        self.assertIn('draftkings', entry['books'])
        # Best over: FD -108 is better than DK -110
        self.assertEqual(entry['best_over_book'], 'fanduel')
        # Best under: DK -110 is better than FD -112
        self.assertEqual(entry['best_under_book'], 'draftkings')
        # Flat over_odds/under_odds = best available
        self.assertEqual(entry['over_odds'], -108)
        self.assertEqual(entry['under_odds'], -110)

    def test_returns_empty_when_no_api_key(self):
        from app.services.nba_service import fetch_player_props_for_event
        with self.app.app_context():
            with patch('app.services.nba_service._get_odds_api_key', return_value=''):
                result = fetch_player_props_for_event('event123')
        self.assertEqual(result, {})


class TestMovementCalc(BaseTestCase):
    """nba_all_props attaches movement delta from OddsSnapshot."""

    def test_movement_attached_when_snapshot_exists(self):
        self.register_and_login()
        today = date.today()

        with self.app.app_context():
            # Insert an earlier snapshot with a different line
            snap = OddsSnapshot(
                game_id='g123',
                game_date=today,
                player_name='LeBron James',
                market='player_points',
                bookmaker='fanduel',
                line=25.0,   # earlier line
                over_odds=-110,
                under_odds=-110,
                snapped_at=datetime.now(timezone.utc) - timedelta(hours=3),
            )
            db.session.add(snap)
            db.session.commit()

        mock_game = {
            'espn_id': 'g123',
            'odds_event_id': 'ev123',
            'away': {'name': 'Lakers', 'abbr': 'LAL'},
            'home': {'name': 'Celtics', 'abbr': 'BOS'},
            'start_time': '2026-03-08T20:00:00Z',
        }
        mock_prop = {
            'player_points': [{
                'player': 'LeBron James',
                'line': 26.0,   # current line (moved up 1.0)
                'over_odds': -108,
                'under_odds': -112,
                'books': {'fanduel': {'over_odds': -108, 'under_odds': -112}},
                'best_over_book': 'fanduel',
                'best_under_book': 'fanduel',
            }]
        }

        with patch('app.routes.bet.get_todays_games', return_value=[mock_game]), \
             patch('app.routes.bet.fetch_player_props_for_event', return_value=mock_prop), \
             patch('app.routes.bet._resolve_player_team_abbrs', return_value={'LeBron James': 'LAL'}):
            resp = self.client.get('/nba/all-props')

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data), 1)
        movement = data[0].get('movement')
        self.assertIsNotNone(movement)
        self.assertEqual(movement['direction'], 'up')
        self.assertAlmostEqual(movement['line_delta'], 1.0)
        self.assertAlmostEqual(movement['first_line'], 25.0)

    def test_flat_movement_when_no_snapshot(self):
        self.register_and_login()

        mock_game = {
            'espn_id': 'g999',
            'odds_event_id': 'ev999',
            'away': {'name': 'Warriors', 'abbr': 'GSW'},
            'home': {'name': 'Nuggets', 'abbr': 'DEN'},
            'start_time': '2026-03-08T22:00:00Z',
        }
        mock_prop = {
            'player_points': [{
                'player': 'Steph Curry',
                'line': 28.5,
                'over_odds': -110,
                'under_odds': -110,
                'books': {},
                'best_over_book': '',
                'best_under_book': '',
            }]
        }

        with patch('app.routes.bet.get_todays_games', return_value=[mock_game]), \
             patch('app.routes.bet.fetch_player_props_for_event', return_value=mock_prop), \
             patch('app.routes.bet._resolve_player_team_abbrs', return_value={}):
            resp = self.client.get('/nba/all-props')

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data), 1)
        movement = data[0].get('movement')
        self.assertIsNotNone(movement)
        self.assertEqual(movement['direction'], 'flat')
        self.assertEqual(movement['line_delta'], 0)


class TestSnapshotJob(BaseTestCase):
    """snapshot_todays_props runs without error in app context."""

    def test_snapshot_todays_props_inserts_rows(self):
        from app.services.nba_service import snapshot_todays_props

        mock_game = {
            'espn_id': 'g_snap1',
            'odds_event_id': 'ev_snap1',
            'away': {'name': 'Lakers', 'abbr': 'LAL'},
            'home': {'name': 'Celtics', 'abbr': 'BOS'},
            'start_time': '2026-03-08T20:00:00Z',
        }
        mock_props = {
            'player_points': [{
                'player': 'LeBron James',
                'line': 25.5,
                'over_odds': -108,
                'under_odds': -112,
                'books': {
                    'fanduel': {'over_odds': -108, 'under_odds': -112},
                    'draftkings': {'over_odds': -110, 'under_odds': -110},
                },
                'best_over_book': 'fanduel',
                'best_under_book': 'draftkings',
            }]
        }

        with self.app.app_context():
            with patch('app.services.nba_service.get_todays_games', return_value=[mock_game]), \
                 patch('app.services.nba_service.fetch_player_props_for_event', return_value=mock_props):
                count = snapshot_todays_props()

            self.assertEqual(count, 2)   # 2 books
            snaps = OddsSnapshot.query.all()
            self.assertEqual(len(snaps), 2)
            books = {s.bookmaker for s in snaps}
            self.assertIn('fanduel', books)
            self.assertIn('draftkings', books)

    def test_snapshot_skips_recent_duplicates(self):
        from app.services.nba_service import snapshot_todays_props
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo('America/New_York')).date()

        with self.app.app_context():
            # Pre-insert a recent snapshot
            existing = OddsSnapshot(
                game_id='g_dup',
                game_date=today,
                player_name='Kevin Durant',
                market='player_points',
                bookmaker='fanduel',
                line=28.0,
                over_odds=-110,
                under_odds=-110,
                snapped_at=datetime.now(timezone.utc),  # just now
            )
            db.session.add(existing)
            db.session.commit()

        mock_game = {
            'espn_id': 'g_dup',
            'odds_event_id': 'ev_dup',
            'away': {'name': 'Nets', 'abbr': 'BKN'},
            'home': {'name': 'Knicks', 'abbr': 'NYK'},
            'start_time': '2026-03-08T20:00:00Z',
        }
        mock_props = {
            'player_points': [{
                'player': 'Kevin Durant',
                'line': 28.5,
                'over_odds': -110,
                'under_odds': -110,
                'books': {
                    'fanduel': {'over_odds': -110, 'under_odds': -110},
                },
                'best_over_book': 'fanduel',
                'best_under_book': 'fanduel',
            }]
        }

        with self.app.app_context():
            with patch('app.services.nba_service.get_todays_games', return_value=[mock_game]), \
                 patch('app.services.nba_service.fetch_player_props_for_event', return_value=mock_props):
                count = snapshot_todays_props()

            self.assertEqual(count, 0)   # skipped because recent snap exists


class TestParlaySubmitWithPropFields(BaseTestCase):
    """Backend accepts leg JSON with player prop fields."""

    def test_submit_parlay_with_prop_legs(self):
        self.register_and_login()
        payload = {
            'stake': 20.0,
            'outcome': 'pending',
            'legs': [
                {
                    'team_a': 'Lakers',
                    'team_b': 'Celtics',
                    'match_date': '2026-03-08',
                    'bet_type': 'over',
                    'player_name': 'LeBron James',
                    'prop_type': 'player_points',
                    'prop_line': 25.5,
                    'game_id': 'g1',
                },
                {
                    'team_a': 'Warriors',
                    'team_b': 'Nuggets',
                    'match_date': '2026-03-08',
                    'bet_type': 'under',
                    'player_name': 'Steph Curry',
                    'prop_type': 'player_points',
                    'prop_line': 28.5,
                    'game_id': 'g2',
                },
            ],
        }
        mock_score = {
            'edge': 0.0, 'edge_over': 0.0, 'edge_under': 0.0,
            'projection': 25.0, 'confidence_tier': 'no_edge',
        }
        mock_detector_instance = MagicMock()
        mock_detector_instance.score_prop.return_value = mock_score
        mock_detector_cls = MagicMock(return_value=mock_detector_instance)
        with patch('app.routes.bet.ProjectionEngine'), \
             patch('app.routes.bet.ValueDetector', mock_detector_cls):
            resp = self.client.post(
                '/bets/parlay',
                data=json.dumps(payload),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data.get('success'))
        self.assertIn('2 leg', data.get('message', ''))

        with self.app.app_context():
            bets = Bet.query.filter_by(is_parlay=True).all()
            self.assertEqual(len(bets), 2)
            players = {b.player_name for b in bets}
            self.assertIn('LeBron James', players)
            self.assertIn('Steph Curry', players)


class TestNewBetFormParlayTab(BaseTestCase):
    """Parlay tab HTML has the new card grid elements."""

    def test_parlay_tab_has_card_grid_elements(self):
        self.register_and_login()
        resp = self.client.get('/bets/new')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="parlay-prop-grid"', resp.data)
        self.assertIn(b'id="parlay-selected-legs"', resp.data)
        self.assertIn(b'id="parlay-game-filter"', resp.data)
        self.assertIn(b'id="parlay-legs-count"', resp.data)

    def test_parlay_tab_keeps_wager_form(self):
        self.register_and_login()
        resp = self.client.get('/bets/new')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="parlay-form"', resp.data)
        self.assertIn(b'id="parlay-stake"', resp.data)
        self.assertIn(b'id="parlay-outcome"', resp.data)
        self.assertIn(b'id="parlay-bonus-mult"', resp.data)
        self.assertIn(b'id="parlay-feedback"', resp.data)

    def test_all_props_response_includes_books_and_movement(self):
        self.register_and_login()
        mock_game = {
            'espn_id': 'g_test',
            'odds_event_id': 'ev_test',
            'away': {'name': 'Lakers', 'abbr': 'LAL'},
            'home': {'name': 'Celtics', 'abbr': 'BOS'},
            'start_time': '2026-03-08T20:00:00Z',
        }
        mock_prop = {
            'player_points': [{
                'player': 'LeBron James',
                'line': 25.5,
                'over_odds': -108,
                'under_odds': -112,
                'books': {
                    'fanduel': {'over_odds': -108, 'under_odds': -112},
                    'draftkings': {'over_odds': -110, 'under_odds': -110},
                },
                'best_over_book': 'fanduel',
                'best_under_book': 'draftkings',
            }]
        }
        with patch('app.routes.bet.get_todays_games', return_value=[mock_game]), \
             patch('app.routes.bet.fetch_player_props_for_event', return_value=mock_prop), \
             patch('app.routes.bet._resolve_player_team_abbrs', return_value={'LeBron James': 'LAL'}):
            resp = self.client.get('/nba/all-props')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(len(data), 1)
        prop = data[0]
        self.assertIn('books', prop)
        self.assertIn('best_over_book', prop)
        self.assertIn('best_under_book', prop)
        self.assertIn('movement', prop)
        self.assertIn('fanduel', prop['books'])
        self.assertIn('draftkings', prop['books'])
