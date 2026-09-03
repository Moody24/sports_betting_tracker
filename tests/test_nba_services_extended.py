"""Focused nba services extended tests split from the legacy service suite."""

import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from app import db
from app.models import Bet
from tests.helpers import BaseTestCase, make_bet, make_user


class TestNBAService(BaseTestCase):
    """Tests for nba_service.py ESPN data-fetch helpers."""

    def _make_espn_scoreboard_response(self):
        return {
            "events": [
                {
                    "id": "401234567",
                    "name": "Lakers vs Celtics",
                    "date": "2026-06-25T00:00:00Z",
                    "competitions": [{
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "110",
                                "team": {
                                    "displayName": "Los Angeles Lakers",
                                    "abbreviation": "LAL",
                                    "logo": "https://example.com/lal.png",
                                }
                            },
                            {
                                "homeAway": "away",
                                "score": "105",
                                "team": {
                                    "displayName": "Boston Celtics",
                                    "abbreviation": "BOS",
                                    "logo": "https://example.com/bos.png",
                                }
                            },
                        ]
                    }],
                    "status": {
                        "displayClock": "0:00",
                        "period": 4,
                        "type": {
                            "name": "STATUS_FINAL",
                            "detail": "Final",
                            "description": "Final",
                        }
                    }
                }
            ]
        }

    @patch('app.services.nba_service.requests.get')
    def test_fetch_espn_scoreboard_parses_games(self, mock_get):
        """fetch_espn_scoreboard returns parsed game list from ESPN JSON."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_espn_scoreboard_response()
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        from app.services.nba_service import fetch_espn_scoreboard
        with self.app.app_context():
            games = fetch_espn_scoreboard()

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]['espn_id'], '401234567')
        self.assertEqual(games[0]['home']['name'], 'Los Angeles Lakers')
        self.assertEqual(games[0]['away']['name'], 'Boston Celtics')
        self.assertEqual(games[0]['home']['score'], 110)
        self.assertEqual(games[0]['status'], 'STATUS_FINAL')

    @patch('app.services.nba_service.requests.get')
    def test_fetch_espn_scoreboard_returns_empty_on_error(self, mock_get):
        """fetch_espn_scoreboard returns [] when request fails."""
        import requests as _req
        mock_get.side_effect = _req.RequestException("network error")
        from app.services.nba_service import fetch_espn_scoreboard
        with self.app.app_context():
            games = fetch_espn_scoreboard()
        self.assertEqual(games, [])

    @patch('app.services.nba_service.requests.get')
    def test_fetch_espn_boxscore_parses_player_stats(self, mock_get):
        """fetch_espn_boxscore returns player stats dict."""
        from app.config_display import PROP_ESPN_COLUMN
        # Build minimal ESPN summary response
        col_names = list(PROP_ESPN_COLUMN.values())[:3]
        mock_data = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": col_names,
                        "athletes": [{
                            "athlete": {"displayName": "LeBron James"},
                            "stats": ["28", "8", "9"],
                        }]
                    }]
                }]
            }
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        from app.services.nba_service import fetch_espn_boxscore
        with self.app.app_context():
            result = fetch_espn_boxscore('401234567')

        self.assertIn('LeBron James', result)

    @patch('app.services.nba_service.requests.get')
    def test_fetch_espn_boxscore_returns_empty_on_error(self, mock_get):
        """fetch_espn_boxscore returns {} when request fails."""
        import requests as _req
        mock_get.side_effect = _req.RequestException("timeout")
        from app.services.nba_service import fetch_espn_boxscore
        with self.app.app_context():
            result = fetch_espn_boxscore('abc')
        self.assertEqual(result, {})

    @patch('app.services.nba_service.requests.get')
    def test_fetch_odds_combined_returns_empty_without_key(self, mock_get):
        """fetch_odds_combined returns ({}, {}) when ODDS_API_KEY is not set."""
        with patch.dict(os.environ, {'ODDS_API_KEY': ''}):
            from app.services.nba_service import fetch_odds_combined
            totals, h2h, _spreads = fetch_odds_combined()
        self.assertEqual(totals, {})
        self.assertEqual(h2h, {})
        mock_get.assert_not_called()

    @patch('app.services.nba_service.requests.get')
    def test_fetch_odds_combined_parses_totals(self, mock_get):
        """fetch_odds_combined extracts over/under lines from Odds API response."""
        mock_data = [{
            "home_team": "Los Angeles Lakers",
            "away_team": "Boston Celtics",
            "bookmakers": [{
                "markets": [{
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 225.5},
                        {"name": "Under", "point": 225.5},
                    ]
                }]
            }]
        }]
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        with patch.dict(os.environ, {'ODDS_API_KEY': 'test_key_123'}):
            from app.services.nba_service import fetch_odds_combined
            totals, h2h, _spreads = fetch_odds_combined()

        self.assertTrue(len(totals) > 0)
        line = list(totals.values())[0]
        self.assertAlmostEqual(line, 225.5)

    def test_matchup_key_is_sorted(self):
        """_matchup_key returns a sorted tuple for consistent lookup."""
        from app.services.nba_service import _matchup_key
        k1 = _matchup_key('Los Angeles Lakers', 'Boston Celtics')
        k2 = _matchup_key('Boston Celtics', 'Los Angeles Lakers')
        self.assertEqual(k1, k2)

    def test_normalize_team_name_aliases(self):
        """_normalize_team_name resolves known aliases."""
        from app.services.nba_service import _normalize_team_name
        self.assertEqual(_normalize_team_name('LA Clippers'), 'los angeles clippers')
        self.assertEqual(_normalize_team_name('GS Warriors'), 'golden state warriors')


class TestNBALiveHelpers(BaseTestCase):
    """Tests for nba_live.py route helpers that don't require full ESPN calls."""

    def test_normalize_name_lowercases_and_strips(self):
        """_normalize_name lowercases, removes punctuation, strips whitespace."""
        from app.routes.nba_live import _normalize_name
        self.assertEqual(_normalize_name('LeBron James'), 'lebron james')
        self.assertEqual(_normalize_name('  L.James  '), 'l james')
        self.assertEqual(_normalize_name(''), '')

    def test_clock_to_seconds_converts_correctly(self):
        """_clock_to_seconds converts MM:SS to integer seconds."""
        from app.routes.nba_live import _clock_to_seconds
        self.assertEqual(_clock_to_seconds('5:30'), 330)
        self.assertEqual(_clock_to_seconds('0:00'), 0)
        self.assertEqual(_clock_to_seconds('12:00'), 720)

    def test_clock_to_seconds_bad_input(self):
        """_clock_to_seconds returns 0 for bad input."""
        from app.routes.nba_live import _clock_to_seconds
        self.assertEqual(_clock_to_seconds(''), 0)
        self.assertEqual(_clock_to_seconds(None), 0)
        self.assertEqual(_clock_to_seconds('notavalue'), 0)

    def test_estimate_elapsed_ratio_final(self):
        """_estimate_elapsed_ratio returns 1.0 for a completed game."""
        from app.routes.nba_live import _estimate_elapsed_ratio
        ratio = _estimate_elapsed_ratio(4, '0:00', 'STATUS_FINAL')
        self.assertAlmostEqual(ratio, 1.0)

    def test_estimate_elapsed_ratio_pregame(self):
        """_estimate_elapsed_ratio returns 0.0 before tip-off."""
        from app.routes.nba_live import _estimate_elapsed_ratio
        ratio = _estimate_elapsed_ratio(None, '', 'pregame')
        self.assertAlmostEqual(ratio, 0.0)

    def test_extract_prop_boxscore_parses_players(self):
        """_extract_prop_boxscore returns dict keyed by player name."""
        from app.services.espn_client import extract_prop_boxscore
        from app.config_display import PROP_ESPN_COLUMN
        col_names = list(PROP_ESPN_COLUMN.values())[:2]
        summary_data = {
            "boxscore": {
                "players": [{
                    "statistics": [{
                        "names": col_names,
                        "athletes": [{
                            "athlete": {"displayName": "Jayson Tatum"},
                            "stats": ["30", "8"],
                        }]
                    }]
                }]
            }
        }
        result = extract_prop_boxscore(summary_data)
        self.assertIn('Jayson Tatum', result)

    def test_extract_prop_boxscore_empty_on_missing_key(self):
        """_extract_prop_boxscore returns {} when boxscore key missing."""
        from app.services.espn_client import extract_prop_boxscore
        result = extract_prop_boxscore({})
        self.assertEqual(result, {})

    def test_build_stat_context_no_game(self):
        """_build_stat_context returns minimal ctx when game not found."""
        from app.services.analysis_context import build_stat_context
        with self.app.app_context():
            ctx = build_stat_context({'game_id': 'missing'}, [], def_snap_map={})
        self.assertIn('opp_abbr', ctx)
        self.assertIsNone(ctx.get('opp_def_rating'))

    @patch('app.routes.nba_live.get_todays_games')
    @patch('app.routes.nba_live.fetch_upcoming_games')
    def test_nba_upcoming_games_route_returns_json(self, mock_upcoming, mock_today):
        """GET /nba/upcoming-games returns a JSON list for logged-in users."""
        mock_today.return_value = [{
            'espn_id': '12345',
            'home': {'name': 'Lakers', 'abbr': 'LAL', 'score': 0, 'logo': ''},
            'away': {'name': 'Celtics', 'abbr': 'BOS', 'score': 0, 'logo': ''},
            'status': 'STATUS_SCHEDULED',
            'start_time': '2026-06-25T19:30:00Z',
            'over_under_line': 225.5,
            'moneyline_home': -150,
            'moneyline_away': 130,
        }]
        mock_upcoming.return_value = []
        self.register_and_login()
        resp = self.client.get('/nba/upcoming-games')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['team_b'], 'Lakers')

    @patch('app.routes.nba_live.get_player_props')
    def test_nba_props_route_returns_json(self, mock_props):
        """GET /nba/props/<espn_id> returns JSON props for logged-in users."""
        mock_props.return_value = [{'player': 'LeBron James', 'line': 27.5}]
        self.register_and_login()
        resp = self.client.get('/nba/props/999')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    @patch('app.routes.nba_live.get_todays_games')
    @patch('app.routes.nba_live.fetch_upcoming_games')
    @patch('app.routes.nba_live.recommend_market_sides')
    def test_nba_today_route_redirects_when_not_logged_in(self, mock_recs, mock_upcoming, mock_today):
        """GET /nba/today redirects to login when user is not authenticated."""
        resp = self.client.get('/nba/today')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/auth/login', resp.headers.get('Location', ''))

    def test_nba_prop_progress_requires_player_and_prop(self):
        """GET /nba/prop-progress/<espn_id> returns 400 when params missing."""
        self.register_and_login()
        resp = self.client.get('/nba/prop-progress/12345')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data.get('ok'))

    def test_nba_prop_progress_batch_rejects_empty_body(self):
        self.register_and_login()
        response = self.client.post('/nba/prop-progress/batch', json=[])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['ok'])

    @patch('app.routes.nba_live._resolve_card_progress')
    @patch('app.routes.nba_live.fetch_summary_payload')
    def test_nba_prop_progress_batch_fetches_once_per_game(
        self,
        fetch_summary,
        resolve_progress,
    ):
        fetch_summary.return_value = {'boxscore': {'players': []}}
        resolve_progress.side_effect = [
            {'ok': True, 'actual': 10},
            {'ok': True, 'actual': 5},
        ]
        self.register_and_login()

        response = self.client.post('/nba/prop-progress/batch', json=[
            {
                'card_id': 'card-1',
                'espn_id': 'game-1',
                'player': 'Player A',
                'prop_type': 'player_points',
                'line': 20.5,
                'bet_type': 'over',
            },
            {
                'card_id': 'card-2',
                'espn_id': 'game-1',
                'player': 'Player B',
                'prop_type': 'player_rebounds',
                'line': 7.5,
                'bet_type': 'under',
            },
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {'card-1', 'card-2'})
        fetch_summary.assert_called_once_with('game-1', timeout=8)
        self.assertEqual(resolve_progress.call_count, 2)


class TestNBALiveRouteAdditional(BaseTestCase):
    """Additional tests targeting uncovered branches in nba_live.py routes."""

    @patch('app.routes.nba_live.get_todays_games')
    @patch('app.routes.nba_live.fetch_upcoming_games')
    @patch('app.routes.nba_live.recommend_market_sides')
    def test_nba_today_logged_in_returns_200(self, mock_recs, mock_upcoming, mock_today):
        """GET /nba/today returns 200 for logged-in user."""
        mock_today.return_value = []
        mock_upcoming.return_value = []
        mock_recs.return_value = []
        self.register_and_login()
        resp = self.client.get('/nba/today')
        self.assertEqual(resp.status_code, 200)

    def test_nba_prop_progress_missing_prop_param(self):
        """GET /nba/prop-progress/<id> with player but no prop_type returns 400."""
        self.register_and_login()
        resp = self.client.get('/nba/prop-progress/12345?player=LeBron+James')
        self.assertEqual(resp.status_code, 400)

    @patch('app.services.espn_client.requests.get')
    def test_nba_prop_progress_with_all_params(self, mock_get):
        """GET /nba/prop-progress/<id> with all required params attempts ESPN fetch."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'boxscore': {'players': []}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        self.register_and_login()
        resp = self.client.get(
            '/nba/prop-progress/12345?player=LeBron+James&prop_type=player_points'
        )
        # Should return JSON response (200 or error code, but not a redirect)
        self.assertNotEqual(resp.status_code, 302)


class TestNBAServiceDirect(BaseTestCase):
    """Tests that call nba_service functions directly with mocked ESPN."""

    @patch('app.services.nba_service.fetch_espn_scoreboard')
    def test_backfill_game_snapshots_creates_rows(self, mock_scoreboard):
        """backfill_game_snapshots creates GameSnapshot rows from ESPN data."""
        mock_scoreboard.return_value = [{
            'espn_id': 'test_snap_001',
            'home': {'name': 'Lakers', 'abbr': 'LAL', 'logo': 'http://lal.png', 'score': 110},
            'away': {'name': 'Celtics', 'abbr': 'BOS', 'logo': 'http://bos.png', 'score': 105},
            'status': 'STATUS_FINAL',
            'start_time': '2025-01-01T19:00:00Z',
            'over_under_line': None,
            'moneyline_home': None,
            'moneyline_away': None,
        }]
        from app.services.nba_service import backfill_game_snapshots
        from datetime import date as date_type
        with self.app.app_context():
            result = backfill_game_snapshots(
                start_date=date_type(2025, 1, 1),
                end_date=date_type(2025, 1, 1),
                sleep_seconds=0,
            )
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['scanned_days'], 1)
        self.assertEqual(result['scanned_games'], 1)

    @patch('app.services.nba_service.fetch_espn_scoreboard')
    def test_backfill_game_snapshots_invalid_date_range(self, mock_scoreboard):
        """backfill_game_snapshots returns error for end < start."""
        from app.services.nba_service import backfill_game_snapshots
        from datetime import date as date_type
        with self.app.app_context():
            result = backfill_game_snapshots(
                start_date=date_type(2025, 1, 5),
                end_date=date_type(2025, 1, 1),
                sleep_seconds=0,
            )
        self.assertEqual(result['error'], 'invalid_date_range')
        mock_scoreboard.assert_not_called()

    @patch('app.services.nba_service.fetch_espn_scoreboard')
    def test_backfill_game_snapshots_include_existing(self, mock_scoreboard):
        """backfill_game_snapshots updates existing snapshots when include_existing=True."""
        from app.models import GameSnapshot
        from app.services.nba_service import backfill_game_snapshots
        from datetime import date as date_type

        game_date = date_type(2025, 1, 2)
        mock_scoreboard.return_value = [{
            'espn_id': 'test_snap_002',
            'home': {'name': 'Warriors', 'abbr': 'GSW', 'logo': '', 'score': 120},
            'away': {'name': 'Nets', 'abbr': 'BKN', 'logo': '', 'score': 110},
            'status': 'STATUS_FINAL',
            'start_time': '2025-01-02T19:00:00Z',
            'over_under_line': None,
            'moneyline_home': None,
            'moneyline_away': None,
        }]
        with self.app.app_context():
            # Create existing snapshot
            snap = GameSnapshot(
                espn_id='test_snap_002',
                game_date=game_date,
                home_team='Warriors',
                away_team='Nets',
                status='STATUS_SCHEDULED',
                is_final=False,
            )
            db.session.add(snap)
            db.session.commit()

            result = backfill_game_snapshots(
                start_date=game_date,
                end_date=game_date,
                include_existing=True,
                sleep_seconds=0,
            )
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['updated'], 1)

    @patch('app.services.nba_service.fetch_espn_scoreboard')
    def test_backfill_game_snapshots_enriches_historical_bet_lines(
        self,
        mock_scoreboard,
    ):
        from app.models import GameSnapshot
        from app.services.nba_service import backfill_game_snapshots

        game_date = date(2025, 1, 3)
        mock_scoreboard.return_value = [{
            'espn_id': 'test_snap_003',
            'home': {'name': 'Lakers', 'logo': '', 'score': 110},
            'away': {'name': 'Celtics', 'logo': '', 'score': 105},
            'status': 'STATUS_FINAL',
        }]
        with self.app.app_context():
            user = make_user('line_user', 'line@test.com')
            db.session.add(user)
            db.session.flush()
            for bet in (
                make_bet(
                    user.id,
                    match_date=datetime(2025, 1, 3, tzinfo=timezone.utc),
                    bet_type='over',
                    over_under_line=221.5,
                ),
                make_bet(
                    user.id,
                    match_date=datetime(2025, 1, 3, tzinfo=timezone.utc),
                    picked_team='Lakers',
                    american_odds=-130,
                ),
                make_bet(
                    user.id,
                    match_date=datetime(2025, 1, 3, tzinfo=timezone.utc),
                    picked_team='Celtics',
                    american_odds=115,
                ),
            ):
                db.session.add(bet)
            db.session.commit()

            result = backfill_game_snapshots(game_date, game_date, sleep_seconds=0)
            stored = GameSnapshot.query.one()

        self.assertEqual(result['ou_filled'], 1)
        self.assertEqual(result['moneyline_filled'], 1)
        self.assertEqual(stored.over_under_line, 221.5)
        self.assertEqual(stored.moneyline_home, -130)
        self.assertEqual(stored.moneyline_away, 115)

    @patch('app.services.nba_service._fetch_historical_odds_for_date')
    def test_ingest_historical_market_odds_respects_force(
        self,
        fetch_historical,
    ):
        from app.models import GameSnapshot
        from app.services.nba_service import ingest_historical_market_odds

        game_date = date(2025, 1, 4)
        payload = [{
            'home_team': 'Lakers',
            'away_team': 'Celtics',
            'bookmakers': [{'markets': [
                {'key': 'totals', 'outcomes': [
                    {'name': 'Over', 'point': 222.5},
                ]},
                {'key': 'h2h', 'outcomes': [
                    {'name': 'Lakers', 'price': -135},
                    {'name': 'Celtics', 'price': 120},
                ]},
            ]}],
        }]
        fetch_historical.return_value = (payload, 'ok')
        with self.app.app_context():
            snapshot = GameSnapshot(
                espn_id='historical_001',
                game_date=game_date,
                home_team='Lakers',
                away_team='Celtics',
                over_under_line=220.0,
                moneyline_home=-125,
                moneyline_away=110,
            )
            db.session.add(snapshot)
            db.session.commit()

            unchanged = ingest_historical_market_odds(
                game_date,
                game_date,
                force=False,
                sleep_seconds=0,
            )
            updated = ingest_historical_market_odds(
                game_date,
                game_date,
                force=True,
                sleep_seconds=0,
            )
            stored = GameSnapshot.query.one()

        self.assertEqual(unchanged['matched_snapshots'], 1)
        self.assertEqual(unchanged['ou_updated'], 0)
        self.assertEqual(unchanged['moneyline_updated'], 0)
        self.assertEqual(updated['ou_updated'], 1)
        self.assertEqual(updated['moneyline_updated'], 2)
        self.assertEqual(stored.over_under_line, 222.5)
        self.assertEqual(stored.moneyline_home, -135)
        self.assertEqual(stored.moneyline_away, 120)

    def test_resolve_pending_bets_no_pending(self):
        """resolve_pending_bets returns [] when no bets are pending."""
        from app.services.nba_service import resolve_pending_bets
        with self.app.app_context():
            result = resolve_pending_bets([])
        self.assertEqual(result, [])

    @patch('app.services.nba_service.fetch_espn_scoreboard')
    def test_get_todays_games_uses_mocked_scoreboard(self, mock_scoreboard):
        """get_todays_games returns formatted games from mocked scoreboard."""
        mock_scoreboard.return_value = [{
            'espn_id': 'today_001',
            'home': {'name': 'Heat', 'abbr': 'MIA', 'logo': '', 'score': 0},
            'away': {'name': 'Bulls', 'abbr': 'CHI', 'logo': '', 'score': 0},
            'status': 'STATUS_SCHEDULED',
            'start_time': '2026-06-26T19:00:00Z',
            'over_under_line': 220.5,
            'moneyline_home': -120,
            'moneyline_away': 100,
        }]
        from app.services.nba_service import get_todays_games
        with self.app.app_context():
            with patch('app.services.nba_service.fetch_odds_combined', return_value=({}, {}, {})):
                with patch('app.services.nba_service.fetch_odds_events', return_value={}):
                    games = get_todays_games()
        self.assertIsInstance(games, list)


class TestNBAAnalysisRoutes(BaseTestCase):
    """Tests for nba_analysis.py routes via Flask test client."""

    @patch('app.routes.nba_analysis.get_todays_games')
    @patch('app.routes.nba_analysis.fetch_upcoming_games')
    def test_nba_all_props_empty_returns_json(self, mock_upcoming, mock_today):
        """GET /nba/all-props returns JSON list when no props available."""
        mock_today.return_value = []
        mock_upcoming.return_value = []
        self.register_and_login()
        resp = self.client.get('/nba/all-props')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    @patch('app.routes.nba_analysis.get_todays_games')
    @patch('app.routes.nba_analysis.fetch_player_props_for_event')
    def test_nba_all_props_with_game_and_props(self, mock_props, mock_today):
        """GET /nba/all-props returns props when games and events have data."""
        mock_today.return_value = [{
            'espn_id': 'test_game_123',
            'odds_event_id': 'odds_evt_123',
            'home': {'name': 'Lakers', 'abbr': 'LAL', 'score': 0, 'logo': ''},
            'away': {'name': 'Celtics', 'abbr': 'BOS', 'score': 0, 'logo': ''},
            'status': 'STATUS_SCHEDULED',
            'start_time': '2026-06-26T19:30:00Z',
        }]
        mock_props.return_value = {
            'player_points': [{
                'player': 'LeBron James',
                'line': 27.5,
                'over_odds': -115,
                'under_odds': -105,
                'books': {},
                'best_over_book': 'fanduel',
                'best_under_book': 'draftkings',
            }]
        }
        self.register_and_login()
        resp = self.client.get('/nba/all-props')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    @patch('app.services.score_cache.get_todays_scores')
    def test_nba_analysis_returns_200(self, mock_scores):
        """GET /nba/analysis returns 200 for logged-in user."""
        mock_scores.return_value = []
        self.register_and_login()
        resp = self.client.get('/nba/analysis')
        self.assertEqual(resp.status_code, 200)

    def test_nba_player_analysis_not_found(self):
        """GET /nba/player-analysis/<name> returns error JSON for unknown player."""
        self.register_and_login()
        with patch('app.routes.nba_analysis.find_player_id', return_value=None):
            resp = self.client.get('/nba/player-analysis/Unknown%20Player')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('error', data)

    @patch('app.routes.nba_analysis.find_player_id')
    @patch('app.routes.nba_analysis.get_cached_logs')
    @patch('app.routes.nba_analysis.get_player_stats_summary')
    def test_nba_player_analysis_found(self, mock_summary, mock_logs, mock_find):
        """GET /nba/player-analysis/<name> returns projection JSON when player found."""
        mock_find.return_value = 2544
        mock_logs.return_value = []
        mock_summary.return_value = {'season': {}}
        self.register_and_login()
        resp = self.client.get('/nba/player-analysis/LeBron James?prop_type=player_points')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('player', data)
        self.assertIn('LeBron', data['player'])

    @patch('app.services.score_cache.get_todays_scores')
    def test_nba_stat_analysis_returns_200(self, mock_scores):
        """GET /nba/stat-analysis returns 200 for logged-in user."""
        mock_scores.return_value = []
        self.register_and_login()
        with patch('app.services.nba_service.get_todays_games', return_value=[]):
            resp = self.client.get('/nba/stat-analysis')
        self.assertEqual(resp.status_code, 200)

    def test_stat_analysis_enrichment_does_not_mutate_cached_scores(self):
        """Presentation fields must not leak into the shared score cache."""
        from app.routes.nba_analysis import _enrich_stat_scores

        cached_score = {
            'player': 'LeBron James',
            'prop_type': 'player_points',
            'line': 25.5,
            'confidence_tier': 'strong',
            'win_probability': 0.65,
            'game_id': 'game-1',
        }
        with self.app.app_context():
            enriched = _enrich_stat_scores([cached_score], {})

        self.assertNotIn('indicator', cached_score)
        self.assertEqual(enriched[0]['indicator'], 'strong')

    def test_nba_analysis_redirects_when_not_logged_in(self):
        """GET /nba/analysis redirects unauthenticated users."""
        resp = self.client.get('/nba/analysis')
        self.assertEqual(resp.status_code, 302)

    def test_hit_rates_from_logs_empty(self):
        """_hit_rates_from_logs returns None percentages for empty logs."""
        from app.routes.nba_analysis import _hit_rates_from_logs
        result = _hit_rates_from_logs([], 'pts', 25.0)
        self.assertIsNone(result['over_pct'])

    def test_hit_rates_from_logs_computes_correctly(self):
        """_hit_rates_from_logs computes over/under pct from fake logs."""
        from app.routes.nba_analysis import _hit_rates_from_logs
        logs = []
        for pts_val in [30, 20, 28, 22, 26]:
            log = MagicMock()
            log.pts = pts_val
            log.game_date = date(2025, 1, 1)
            log.matchup = 'vs BOS'
            logs.append(log)
        result = _hit_rates_from_logs(logs, 'pts', 25.0)
        self.assertIsNotNone(result['over_pct'])
        # 30, 28, 26 are >= 25 → 3 over out of 5 = 60%
        self.assertEqual(result['over_pct'], 60)
        self.assertEqual(result['under_pct'], 40)

    def test_compute_hit_rates_unknown_prop_type(self):
        """_compute_hit_rates returns None percentages for unknown prop type."""
        from app.routes.nba_analysis import _compute_hit_rates
        with self.app.app_context():
            result = _compute_hit_rates('LeBron James', 'unknown_prop_xyz', 25.0)
        self.assertIsNone(result['over_pct'])

    def test_resolve_player_team_abbrs_empty(self):
        """_resolve_player_team_abbrs returns {} for empty player_names set."""
        from app.routes.nba_analysis import _resolve_player_team_abbrs
        with self.app.app_context():
            result = _resolve_player_team_abbrs(set())
        self.assertEqual(result, {})


class TestNBAServiceResolve(BaseTestCase):
    """Tests for nba_service.resolve_pending_bets with actual DB bets."""

    @patch('app.services.nba_service.fetch_espn_boxscore')
    def test_resolve_pending_bets_with_pending_bet(self, mock_boxscore):
        """resolve_pending_bets attempts to grade a pending bet."""
        from app.models import User
        from app.services.nba_service import resolve_pending_bets
        mock_boxscore.return_value = {}

        with self.app.app_context():
            user = User(username='resolve_user', email='resolve@test.com')
            user.set_password('pw')
            db.session.add(user)
            db.session.flush()
            bet = Bet(
                user_id=user.id,
                team_a='Lakers', team_b='Celtics',
                bet_amount=25.0,
                bet_type='over',
                match_date=datetime.now(timezone.utc),
                player_name='LeBron James',
                prop_type='player_points',
                prop_line=25.5,
                external_game_id='espn_123',
            )
            db.session.add(bet)
            db.session.commit()

            scoreboard = [{
                'espn_id': 'espn_123',
                'home': {'name': 'Celtics', 'abbr': 'BOS', 'score': 105, 'logo': ''},
                'away': {'name': 'Lakers', 'abbr': 'LAL', 'score': 110, 'logo': ''},
                'status': 'STATUS_FINAL',
                'start_time': '2026-06-26T19:00:00Z',
            }]
            result = resolve_pending_bets(scoreboard)

        self.assertIsInstance(result, list)


class TestResolveCardProgress(BaseTestCase):
    """Tests for resolve_card_progress extracted to nba_service in INFO Batch 2."""

    def _make_summary(self, player_name='LeBron James', pts=22, status='in_progress'):
        return {
            'boxscore': {
                player_name: {'player_points': pts, 'player_rebounds': 7, 'player_assists': 5}
            },
            'game_status': status,
            'period': 3,
            'clock': '5:30',
        }

    def test_resolve_card_no_boxscore_returns_not_ok(self):
        """Returns ok=False when summary has no boxscore."""
        from app.services.nba_service import resolve_card_progress
        result = resolve_card_progress(
            'abc123', 'LeBron James', 'player_points', 22.5, 'over', {}
        )
        self.assertFalse(result['ok'])

    def test_resolve_card_player_not_found(self):
        """Returns ok=False when player name doesn't match boxscore."""
        from app.services.nba_service import resolve_card_progress
        summary = self._make_summary(player_name='Anthony Davis')
        with patch('app.services.nba_service.extract_prop_boxscore',
                   return_value={'Anthony Davis': {'player_points': 20}}):
            with patch('app.services.nba_service.derive_game_status_from_summary',
                       return_value={'elapsed_ratio': 0.5, 'status_text': 'Q3 5:30', 'period': 3, 'clock': '5:30', 'game_state': 'in_progress', 'final': False}):
                result = resolve_card_progress(
                    'abc123', 'Nonexistent Player', 'player_points', 20.5, 'over', summary
                )
        self.assertFalse(result['ok'])

    def test_resolve_card_stat_unavailable(self):
        """Returns ok=False when requested stat not in boxscore for player."""
        from app.services.nba_service import resolve_card_progress
        with patch('app.services.nba_service.extract_prop_boxscore',
                   return_value={'LeBron James': {'player_points': 22}}):
            with patch('app.services.nba_service.derive_game_status_from_summary',
                       return_value={'elapsed_ratio': 0.5, 'status_text': 'Q3 5:30', 'period': 3, 'clock': '5:30', 'game_state': 'in_progress', 'final': False}):
                result = resolve_card_progress(
                    'abc123', 'LeBron James', 'player_rebounds', 7.5, 'over', {}
                )
        self.assertFalse(result['ok'])

    def test_resolve_card_success_over(self):
        """Returns ok=True with on_track=True when projected over line."""
        from app.services.nba_service import resolve_card_progress
        with patch('app.services.nba_service.extract_prop_boxscore',
                   return_value={'LeBron James': {'player_points': 16}}):
            with patch('app.services.nba_service.derive_game_status_from_summary',
                       return_value={'elapsed_ratio': 0.5, 'status_text': 'Q3 5:30', 'period': 3, 'clock': '5:30', 'game_state': 'in_progress', 'final': False}):
                result = resolve_card_progress(
                    'abc123', 'LeBron James', 'player_points', 22.5, 'over', {}
                )
        self.assertTrue(result['ok'])
        self.assertEqual(result['player'], 'LeBron James')
        self.assertEqual(result['current_stat'], 16.0)

    def test_resolve_card_under_on_track(self):
        """on_track=True when projected under line for UNDER bet."""
        from app.services.nba_service import resolve_card_progress
        with patch('app.services.nba_service.extract_prop_boxscore',
                   return_value={'LeBron James': {'player_points': 8}}):
            with patch('app.services.nba_service.derive_game_status_from_summary',
                       return_value={'elapsed_ratio': 0.5, 'status_text': 'Q3 5:30', 'period': 3, 'clock': '5:30', 'game_state': 'in_progress', 'final': False}):
                result = resolve_card_progress(
                    'abc123', 'LeBron James', 'player_points', 22.5, 'under', {}
                )
        self.assertTrue(result['ok'])
        self.assertTrue(result['on_track'])


class TestNBAAnalysisHelpers(BaseTestCase):
    """Tests for pure helper functions in nba_analysis.py."""

    def test_normalize_name_strips_special_chars(self):
        from app.routes.nba_analysis import _normalize_name
        self.assertEqual(_normalize_name("LeBron James!"), "lebron james")

    def test_normalize_name_empty(self):
        from app.routes.nba_analysis import _normalize_name
        self.assertEqual(_normalize_name(""), "")

    def test_normalize_name_none(self):
        from app.routes.nba_analysis import _normalize_name
        self.assertEqual(_normalize_name(None), "")

    def test_hit_rates_empty_logs(self):
        from app.routes.nba_analysis import _hit_rates_from_logs
        result = _hit_rates_from_logs([], 'pts', 20.0)
        self.assertIsNone(result['over_pct'])
        self.assertEqual(result['sample'], 0)

    def test_hit_rates_no_col_name(self):
        from app.routes.nba_analysis import _hit_rates_from_logs
        result = _hit_rates_from_logs([MagicMock()], None, 20.0)
        self.assertIsNone(result['over_pct'])

    def test_hit_rates_all_none_values(self):
        """Returns empty result when all log values are None."""
        from app.routes.nba_analysis import _hit_rates_from_logs
        log = MagicMock()
        log.game_date = date(2026, 1, 1)
        log.matchup = 'LAL vs BOS'
        log.pts = None
        setattr(log, 'pts', None)
        result = _hit_rates_from_logs([log], 'pts', 20.0)
        self.assertIsNone(result['over_pct'])

    def test_hit_rates_over_and_under(self):
        """Correctly computes over/under pct from log values."""
        from app.routes.nba_analysis import _hit_rates_from_logs
        logs = []
        for pts in [25.0, 18.0, 30.0, 15.0]:
            log = MagicMock()
            log.game_date = date(2026, 1, 1)
            log.matchup = 'LAL vs BOS'
            setattr(log, 'pts', pts)
            logs.append(log)
        result = _hit_rates_from_logs(logs, 'pts', 20.0)
        self.assertEqual(result['sample'], 4)
        self.assertEqual(result['over_pct'], 50)
        self.assertEqual(result['under_pct'], 50)
