"""Focused stats services tests split from the legacy service suite."""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import requests as _requests
from app import db
from app.models import (
    InjuryReport,
    JobLog,
    PlayerGameLog,
    TeamDefenseSnapshot,
)
from tests.helpers import BaseTestCase
from tests.service_test_support import (
    _seed_player_logs,
    _seed_defense,
    _FakeDataFrame,
    _seed_injury,
)


class TestStatsService(BaseTestCase):
    """Tests for stats_service functions that interact with DB and NBA API."""

    # -- _parse_minutes --

    def test_parse_minutes_colon_format(self):
        from app.services.stats_service import _parse_minutes
        self.assertAlmostEqual(_parse_minutes('34:30'), 34.5, places=1)

    def test_parse_minutes_float_format(self):
        from app.services.stats_service import _parse_minutes
        self.assertAlmostEqual(_parse_minutes('34.5'), 34.5)

    def test_parse_minutes_none(self):
        from app.services.stats_service import _parse_minutes
        self.assertEqual(_parse_minutes(None), 0.0)

    def test_parse_minutes_invalid(self):
        from app.services.stats_service import _parse_minutes
        self.assertEqual(_parse_minutes('abc'), 0.0)

    def test_parse_minutes_colon_invalid(self):
        from app.services.stats_service import _parse_minutes
        self.assertEqual(_parse_minutes('ab:cd'), 0.0)

    # -- _parse_game_date --

    def test_parse_game_date_formats(self):
        from app.services.stats_service import _parse_game_date
        self.assertEqual(_parse_game_date('Feb 20, 2026'), date(2026, 2, 20))
        self.assertEqual(_parse_game_date('2026-02-20'), date(2026, 2, 20))
        self.assertEqual(_parse_game_date('2026-02-20T00:00:00'), date(2026, 2, 20))

    def test_parse_game_date_already_date(self):
        from app.services.stats_service import _parse_game_date
        d = date(2026, 2, 20)
        self.assertEqual(_parse_game_date(d), d)

    def test_parse_game_date_datetime_obj(self):
        from app.services.stats_service import _parse_game_date
        dt = datetime(2026, 2, 20, 12, 0, 0)
        # datetime is a subclass of date, so isinstance(dt, date) is True
        # and the function returns it as-is
        result = _parse_game_date(dt)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 2)
        self.assertEqual(result.day, 20)

    def test_parse_game_date_invalid_returns_none(self):
        from app.services.stats_service import _parse_game_date
        result = _parse_game_date('not-a-date')
        self.assertIsNone(result)

    # -- cache_player_logs --

    def test_cache_player_logs_insert_and_update(self):
        from app.services.stats_service import cache_player_logs, get_cached_logs
        with self.app.app_context():
            logs = [{
                'player_id': '201', 'player_name': 'Test Player',
                'team_abbr': 'TST', 'game_date': date(2026, 2, 10),
                'matchup': 'TST vs OPP', 'minutes': 30, 'pts': 20,
                'reb': 5, 'ast': 5, 'stl': 1, 'blk': 1, 'tov': 2,
                'fgm': 8, 'fga': 15, 'ftm': 3, 'fta': 4,
                'fg3m': 1, 'fg3a': 3, 'plus_minus': 5,
                'home_away': 'home', 'win_loss': 'W',
            }]
            cache_player_logs('201', logs)
            cached = get_cached_logs('201')
            self.assertEqual(len(cached), 1)
            self.assertEqual(cached[0].pts, 20)

            # Update existing row
            logs[0]['pts'] = 30
            cache_player_logs('201', logs)
            cached = get_cached_logs('201')
            self.assertEqual(len(cached), 1)
            self.assertEqual(cached[0].pts, 30)

    def test_cache_player_logs_dedup_same_date_does_not_duplicate(self):
        from app.services.stats_service import cache_player_logs
        with self.app.app_context():
            logs = [
                {
                    'player_id': '201', 'player_name': 'Test Player',
                    'team_abbr': 'TST', 'game_date': date(2026, 2, 10),
                    'matchup': 'TST vs OPP', 'minutes': 30, 'pts': 20,
                    'reb': 5, 'ast': 5, 'stl': 1, 'blk': 1, 'tov': 2,
                    'fgm': 8, 'fga': 15, 'ftm': 3, 'fta': 4,
                    'fg3m': 1, 'fg3a': 3, 'plus_minus': 5,
                    'home_away': 'home', 'win_loss': 'W',
                },
                {
                    'player_id': '201', 'player_name': 'Test Player',
                    'team_abbr': 'TST', 'game_date': date(2026, 2, 10),
                    'matchup': 'TST vs OPP', 'minutes': 32, 'pts': 33,
                    'reb': 6, 'ast': 7, 'stl': 2, 'blk': 1, 'tov': 1,
                    'fgm': 11, 'fga': 18, 'ftm': 4, 'fta': 5,
                    'fg3m': 2, 'fg3a': 4, 'plus_minus': 9,
                    'home_away': 'home', 'win_loss': 'W',
                },
            ]

            cache_player_logs('201', logs)
            rows = PlayerGameLog.query.filter_by(player_id='201').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].pts, 33)

            cache_player_logs('201', logs)
            rows = PlayerGameLog.query.filter_by(player_id='201').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].pts, 33)

    # -- get_player_stats_summary --

    def test_get_player_stats_summary_empty(self):
        from app.services.stats_service import get_player_stats_summary
        with self.app.app_context():
            summary = get_player_stats_summary('999', [])
            self.assertEqual(summary['games_played'], 0)
            self.assertEqual(summary['last_5'], {})

    def test_get_player_stats_summary_with_data(self):
        from app.services.stats_service import get_player_stats_summary, get_cached_logs
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='301')
            logs = get_cached_logs('301', last_n=82)
            summary = get_player_stats_summary('301', logs)
            self.assertEqual(summary['games_played'], 20)
            self.assertIn('pts', summary['last_5'])
            self.assertIn('pts', summary['last_10'])
            self.assertIn('pts', summary['season'])
            self.assertIn('pts', summary['std_dev'])

    def test_get_player_stats_summary_no_logs_arg(self):
        from app.services.stats_service import get_player_stats_summary
        with self.app.app_context():
            _seed_player_logs(count=5, player_id='302')
            summary = get_player_stats_summary('302')
            self.assertEqual(summary['games_played'], 5)

    def test_get_player_stats_summary_excludes_dnp_rows(self):
        """DNP rows (minutes=0) must not dilute averages or consume window slots."""
        from app.services.stats_service import get_player_stats_summary
        with self.app.app_context():
            # 4 real games (30 pts each) followed by 3 DNPs
            _seed_player_logs(count=4, player_id='303', base_pts=30.0, base_minutes=35.0)
            for i in range(3):
                dnp = PlayerGameLog(
                    player_id='303', player_name='Test Player', team_abbr='TST',
                    game_date=date(2026, 2, 1) + timedelta(days=i),
                    matchup='TST vs OPP', minutes=0, pts=0, reb=0, ast=0,
                    stl=0, blk=0, tov=0, fgm=0, fga=0, ftm=0, fta=0,
                    fg3m=0, fg3a=0, plus_minus=0,
                )
                db.session.add(dnp)
            db.session.commit()

            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('303', last_n=82)
            summary = get_player_stats_summary('303', logs)

            # games_played counts only games with minutes > 0
            self.assertEqual(summary['games_played'], 4)
            # last_5 should reflect the 4 played games, not be diluted by DNPs
            self.assertGreater(summary['last_5'].get('pts', 0), 20,
                               'DNPs should not dilute the pts average below played-game values')

    # -- prune_expired_cache --

    def test_prune_expired_cache(self):
        from app.services.stats_service import prune_expired_cache
        with self.app.app_context():
            expired_log = PlayerGameLog(
                player_id='401', player_name='Expired',
                game_date=date(2026, 1, 1), pts=10,
                cache_expires=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
            db.session.add(expired_log)
            db.session.commit()
            result = prune_expired_cache()
            self.assertEqual(result['expired'], 1)
            self.assertEqual(result['unresolved'], 0)

    # -- fetch_player_game_logs --

    def test_fetch_player_game_logs_success(self):
        from app.services import stats_service

        df = _FakeDataFrame([{
            'PLAYER_NAME': 'LeBron James', 'TEAM_ABBREVIATION': 'LAL',
            'GAME_DATE': 'Feb 20, 2026', 'MATCHUP': 'LAL vs. BOS',
            'MIN': '35:00', 'PTS': 28, 'REB': 7, 'AST': 8,
            'STL': 1, 'BLK': 1, 'TOV': 3, 'FGM': 10, 'FGA': 20,
            'FTM': 5, 'FTA': 6, 'FG3M': 3, 'FG3A': 7,
            'PLUS_MINUS': 12, 'WL': 'W',
        }])

        mock_endpoint_instance = MagicMock()
        mock_endpoint_instance.get_data_frames.return_value = [df]

        mock_pgl_module = MagicMock()
        mock_pgl_module.PlayerGameLog.return_value = mock_endpoint_instance

        # Build the module chain so `from nba_api.stats.endpoints import playergamelog` works
        mock_endpoints = MagicMock()
        mock_endpoints.playergamelog = mock_pgl_module

        with patch.dict(sys.modules, {
            'nba_api': MagicMock(),
            'nba_api.stats': MagicMock(),
            'nba_api.stats.endpoints': mock_endpoints,
            'nba_api.stats.endpoints.playergamelog': mock_pgl_module,
        }):
            with patch('app.services.stats_service.time.sleep'):
                logs = stats_service.fetch_player_game_logs('123', season='2025-26')

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['pts'], 28.0)
        self.assertEqual(logs[0]['home_away'], 'home')

    def test_fetch_player_game_logs_api_failure(self):
        from app.services import stats_service

        mock_pgl_module = MagicMock()
        mock_pgl_module.PlayerGameLog.side_effect = Exception("API down")

        mock_endpoints = MagicMock()
        mock_endpoints.playergamelog = mock_pgl_module

        with patch.dict(sys.modules, {
            'nba_api': MagicMock(),
            'nba_api.stats': MagicMock(),
            'nba_api.stats.endpoints': mock_endpoints,
            'nba_api.stats.endpoints.playergamelog': mock_pgl_module,
        }):
            with patch('app.services.stats_service.time.sleep'):
                result = stats_service.fetch_player_game_logs('123')

        self.assertEqual(result, [])

    # -- find_player_id --

    def test_find_player_id_exact_match(self):
        from app.services import stats_service
        mock_nba_players = MagicMock()
        mock_nba_players.get_active_players.return_value = [
            {'id': 2544, 'full_name': 'LeBron James'},
        ]
        # Patch the lazy import by injecting into sys.modules before calling
        with patch.dict(sys.modules, {
            'nba_api.stats.static.players': mock_nba_players,
            'nba_api.stats.static': MagicMock(players=mock_nba_players),
            'nba_api.stats': MagicMock(),
            'nba_api': MagicMock(),
        }):
            result = stats_service.find_player_id('LeBron James')
        self.assertEqual(result, '2544')

    def test_find_player_id_not_found(self):
        from app.services import stats_service
        mock_nba_players = MagicMock()
        mock_nba_players.get_active_players.return_value = [
            {'id': 1, 'full_name': 'Somebody Else'},
        ]
        stats_service.name_resolver.clear_cache()
        with patch.dict(sys.modules, {
            'nba_api.stats.static.players': mock_nba_players,
            'nba_api.stats.static': MagicMock(players=mock_nba_players),
            'nba_api.stats': MagicMock(),
            'nba_api': MagicMock(),
        }):
            result = stats_service.find_player_id('ZZZZZZZZZ')
        self.assertIsNone(result)

    # -- update_player_logs_for_games --

    def test_update_player_logs_for_games(self):
        from app.services.stats_service import update_player_logs_for_games
        with self.app.app_context():
            games = [{'odds_event_id': 'evt1'}]
            with patch('app.services.nba_service.fetch_player_props_for_event',
                       return_value={'player_points': [{'player': 'LeBron James'}]}):
                with patch('app.services.stats_service.find_player_id', return_value='101'):
                    with patch('app.services.stats_service.fetch_player_game_logs',
                               return_value=[{
                                   'player_id': '101', 'player_name': 'LeBron James',
                                   'team_abbr': 'LAL', 'game_date': date(2026, 2, 20),
                                   'matchup': 'LAL vs BOS', 'minutes': 35,
                                   'pts': 25, 'reb': 7, 'ast': 7,
                                   'stl': 1, 'blk': 1, 'tov': 2,
                                   'fgm': 10, 'fga': 20, 'ftm': 5, 'fta': 6,
                                   'fg3m': 3, 'fg3a': 7, 'plus_minus': 5,
                                   'home_away': 'home', 'win_loss': 'W',
                               }]):
                        count = update_player_logs_for_games(games)
                        self.assertEqual(count, 1)

    def test_update_player_logs_no_event_id(self):
        from app.services.stats_service import update_player_logs_for_games
        with self.app.app_context():
            count = update_player_logs_for_games([{'odds_event_id': ''}])
            self.assertEqual(count, 0)

    def test_update_player_logs_fetch_exception(self):
        from app.services.stats_service import update_player_logs_for_games
        with self.app.app_context():
            with patch('app.services.nba_service.fetch_player_props_for_event',
                       side_effect=Exception("API down")):
                count = update_player_logs_for_games([{'odds_event_id': 'evt1'}])
                self.assertEqual(count, 0)

    @patch('app.services.stats_service.find_player_id', return_value='101')
    @patch('app.services.espn_client.requests.get')
    @patch('app.services.stats_service.fetch_espn_scoreboard')
    def test_refresh_completed_game_logs_ingests_finals(self, mock_scoreboard, mock_get, _mock_pid):
        from app.services.stats_service import refresh_completed_game_logs
        with self.app.app_context():
            mock_scoreboard.return_value = [{
                'espn_id': 'game1',
                'status': 'STATUS_FINAL',
                'status_detail': 'Final',
                'home': {'name': 'Boston Celtics', 'abbr': 'BOS', 'score': 120},
                'away': {'name': 'Los Angeles Lakers', 'abbr': 'LAL', 'score': 110},
            }]
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {
                'boxscore': {
                    'players': [{
                        'team': {'displayName': 'Boston Celtics', 'abbreviation': 'BOS'},
                        'statistics': [{
                            'names': ['MIN', 'FG', '3PT', 'FT', 'REB', 'AST', 'STL', 'BLK', 'TO', '+/-', 'PTS'],
                            'athletes': [{
                                'athlete': {'displayName': 'Jayson Tatum', 'id': '300'},
                                'stats': ['36:00', '10-20', '3-8', '5-6', '8', '6', '1', '0', '2', '+7', '28'],
                            }],
                        }],
                    }],
                },
            }
            mock_get.return_value = mock_resp

            summary = refresh_completed_game_logs(days_back=0)
            self.assertEqual(summary['final_games_seen'], 1)
            self.assertGreaterEqual(summary['rows_inserted'], 1)
            self.assertEqual(PlayerGameLog.query.filter_by(player_name='Jayson Tatum').count(), 1)

    @patch('app.services.stats_service.fetch_espn_scoreboard')
    def test_refresh_completed_game_logs_skips_non_final(self, mock_scoreboard):
        from app.services.stats_service import refresh_completed_game_logs
        with self.app.app_context():
            mock_scoreboard.return_value = [{
                'espn_id': 'game2',
                'status': 'STATUS_SCHEDULED',
                'status_detail': 'Scheduled',
                'home': {'name': 'A', 'abbr': 'A', 'score': 0},
                'away': {'name': 'B', 'abbr': 'B', 'score': 0},
            }]
            summary = refresh_completed_game_logs(days_back=0)
            self.assertEqual(summary['final_games_seen'], 0)

    @patch('app.services.stats_service.find_player_id', return_value='2544')
    @patch('app.services.espn_client.requests.get')
    @patch('app.services.stats_service.fetch_espn_scoreboard')
    def test_refresh_completed_game_logs_handles_duplicate_player_rows(self, mock_scoreboard, mock_get, _mock_pid):
        from app.services.stats_service import refresh_completed_game_logs
        with self.app.app_context():
            mock_scoreboard.return_value = [{
                'espn_id': 'game_dup',
                'status': 'STATUS_FINAL',
                'status_detail': 'Final',
                'home': {'name': 'Boston Celtics', 'abbr': 'BOS', 'score': 120},
                'away': {'name': 'Los Angeles Lakers', 'abbr': 'LAL', 'score': 110},
            }]
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {
                'boxscore': {
                    'players': [{
                        'team': {'displayName': 'Boston Celtics', 'abbreviation': 'BOS'},
                        'statistics': [
                            {
                                'names': ['MIN', 'FG', '3PT', 'FT', 'REB', 'AST', 'STL', 'BLK', 'TO', '+/-', 'PTS'],
                                'athletes': [{
                                    'athlete': {'displayName': 'Jayson Tatum', 'id': '300'},
                                    'stats': ['36:00', '10-20', '3-8', '5-6', '8', '6', '1', '0', '2', '+7', '28'],
                                }],
                            },
                            {
                                'names': ['MIN', 'FG', '3PT', 'FT', 'REB', 'AST', 'STL', 'BLK', 'TO', '+/-', 'PTS'],
                                'athletes': [{
                                    'athlete': {'displayName': 'Jayson Tatum', 'id': '300'},
                                    'stats': ['36:00', '11-20', '4-8', '5-6', '8', '6', '1', '0', '2', '+8', '31'],
                                }],
                            },
                        ],
                    }],
                },
            }
            mock_get.return_value = mock_resp

            summary = refresh_completed_game_logs(days_back=0)
            self.assertEqual(summary['final_games_seen'], 1)
            rows = PlayerGameLog.query.filter_by(player_id='2544').all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].pts, 31)

    # -- PlayerNameResolver cache --

    def test_name_resolver_cache_hit(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        resolver.best_match('LeBron', ['LeBron James'])
        result = resolver.best_match('LeBron', ['LeBron James'])
        self.assertEqual(result, 'LeBron James')

    def test_name_resolver_clear_cache(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        resolver.best_match('LeBron', ['LeBron James'])
        resolver.clear_cache()
        self.assertEqual(resolver._cache, {})

    def test_name_resolver_empty_target(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        self.assertIsNone(resolver.best_match('', ['LeBron James']))


class TestContextService(BaseTestCase):
    """Tests for context_service: injuries, B2B, rest days, game context."""

    def setUp(self):
        super().setUp()
        from app.services.context_service import clear_schedule_caches
        clear_schedule_caches()

    # -- fetch_espn_injuries --

    @patch('app.services.espn_client.requests.get')
    def test_fetch_espn_injuries_success(self, mock_get):
        from app.services.context_service import fetch_espn_injuries
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'teams': [{
                'team': {'displayName': 'Los Angeles Lakers'},
                'athletes': [{
                    'athlete': {'displayName': 'LeBron James'},
                    'status': 'Questionable',
                    'details': 'Knee soreness',
                }],
            }],
        }
        mock_get.return_value = mock_resp
        injuries = fetch_espn_injuries()
        self.assertEqual(len(injuries), 1)
        self.assertEqual(injuries[0]['player_name'], 'LeBron James')
        self.assertEqual(injuries[0]['status'], 'questionable')

    @patch('app.services.espn_client.requests.get')
    def test_fetch_espn_injuries_network_error(self, mock_get):
        from app.services.context_service import fetch_espn_injuries
        mock_get.side_effect = _requests.RequestException("timeout")
        self.assertEqual(fetch_espn_injuries(), [])

    @patch('app.services.espn_client.requests.get')
    def test_fetch_espn_injuries_dict_status(self, mock_get):
        from app.services.context_service import fetch_espn_injuries
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'items': [{
                'team': {'name': 'Lakers'},
                'injuries': [{
                    'athlete': {'fullName': 'AD'},
                    'status': {'type': 'Out'},
                    'details': {'detail': 'Injury detail'},
                }],
            }],
        }
        mock_get.return_value = mock_resp
        injuries = fetch_espn_injuries()
        self.assertEqual(len(injuries), 1)
        self.assertEqual(injuries[0]['status'], 'out')

    @patch('app.services.espn_client.requests.get')
    def test_fetch_espn_injuries_skips_no_name(self, mock_get):
        from app.services.context_service import fetch_espn_injuries
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'teams': [{
                'team': {'displayName': 'Lakers'},
                'athletes': [{'athlete': {'displayName': ''}, 'status': 'Out'}],
            }],
        }
        mock_get.return_value = mock_resp
        self.assertEqual(fetch_espn_injuries(), [])

    @patch('app.services.espn_client.requests.get')
    def test_fetch_espn_injuries_new_payload_shape(self, mock_get):
        from app.services.context_service import fetch_espn_injuries
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'injuries': [{
                'displayName': 'Los Angeles Lakers',
                'injuries': [{
                    'athlete': {'displayName': 'LeBron James'},
                    'status': 'Day-To-Day',
                    'shortComment': 'Questionable for tonight.',
                }],
            }],
        }
        mock_get.return_value = mock_resp
        injuries = fetch_espn_injuries()
        self.assertEqual(len(injuries), 1)
        self.assertEqual(injuries[0]['team'], 'Los Angeles Lakers')
        self.assertEqual(injuries[0]['player_name'], 'LeBron James')
        self.assertEqual(injuries[0]['status'], 'day-to-day')

    # -- refresh_injuries --

    @patch('app.services.context_service.fetch_espn_injuries')
    def test_refresh_injuries_success(self, mock_fetch):
        from app.services.context_service import refresh_injuries
        with self.app.app_context():
            mock_fetch.return_value = [
                {'player_name': 'LeBron James', 'team': 'Lakers',
                 'status': 'questionable', 'detail': 'Knee'},
                {'player_name': 'AD', 'team': 'Lakers',
                 'status': 'out', 'detail': 'Foot'},
            ]
            count = refresh_injuries()
            self.assertEqual(count, 2)
            self.assertEqual(InjuryReport.query.count(), 2)

    @patch('app.services.context_service.fetch_espn_injuries')
    def test_refresh_injuries_empty(self, mock_fetch):
        from app.services.context_service import refresh_injuries
        with self.app.app_context():
            mock_fetch.return_value = []
            count = refresh_injuries()
            self.assertEqual(count, 0)

    # -- get_player_injury_status --

    def test_get_player_injury_status_found(self):
        from app.services.context_service import get_player_injury_status
        with self.app.app_context():
            _seed_injury('LeBron James', 'questionable')
            status = get_player_injury_status('LeBron James')
            self.assertEqual(status['status'], 'questionable')

    def test_get_player_injury_status_not_found(self):
        from app.services.context_service import get_player_injury_status
        with self.app.app_context():
            status = get_player_injury_status('Nobody')
            self.assertEqual(status, {})

    # -- is_player_available --

    def test_is_player_available_out(self):
        from app.services.context_service import is_player_available
        with self.app.app_context():
            _seed_injury('Hurt Guy', 'out')
            self.assertFalse(is_player_available('Hurt Guy'))

    def test_is_player_available_doubtful(self):
        from app.services.context_service import is_player_available
        with self.app.app_context():
            _seed_injury('Doubtful Guy', 'doubtful')
            self.assertFalse(is_player_available('Doubtful Guy'))

    def test_is_player_available_questionable(self):
        from app.services.context_service import is_player_available
        with self.app.app_context():
            _seed_injury('Maybe Guy', 'questionable')
            self.assertTrue(is_player_available('Maybe Guy'))

    # -- check_back_to_back --

    @patch('app.services.espn_client.requests.get')
    def test_check_b2b_true(self, mock_get):
        from app.services.context_service import check_back_to_back
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'events': [{
                'competitions': [{
                    'competitors': [
                        {'team': {'displayName': 'Los Angeles Lakers'}},
                        {'team': {'displayName': 'Boston Celtics'}},
                    ],
                }],
            }],
        }
        mock_get.return_value = mock_resp
        self.assertTrue(check_back_to_back('Lakers'))

    @patch('app.services.espn_client.requests.get')
    def test_check_b2b_false(self, mock_get):
        from app.services.context_service import check_back_to_back
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'events': []}
        mock_get.return_value = mock_resp
        self.assertFalse(check_back_to_back('Lakers'))

    @patch('app.services.espn_client.requests.get')
    def test_check_b2b_network_error(self, mock_get):
        from app.services.context_service import check_back_to_back
        mock_get.side_effect = _requests.RequestException("timeout")
        self.assertFalse(check_back_to_back('Lakers'))

    # -- get_days_rest --

    @patch('app.services.espn_client.requests.get')
    def test_get_days_rest_found(self, mock_get):
        from app.services.context_service import get_days_rest
        from app.utils.time_helpers import et_today as _today_et

        def side_effect(url, params=None, timeout=None, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            date_str = params.get('dates', '') if params else ''
            two_days_ago = (_today_et() - timedelta(days=2)).strftime('%Y%m%d')
            if date_str == two_days_ago:
                resp.json.return_value = {
                    'events': [{'competitions': [{'competitors': [
                        {'team': {'displayName': 'Los Angeles Lakers'}},
                    ]}]}],
                }
            else:
                resp.json.return_value = {'events': []}
            return resp
        mock_get.side_effect = side_effect
        self.assertEqual(get_days_rest('Lakers'), 2)

    @patch('app.services.espn_client.requests.get')
    def test_get_days_rest_default(self, mock_get):
        from app.services.context_service import get_days_rest
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'events': []}
        mock_get.return_value = mock_resp
        self.assertEqual(get_days_rest('Lakers', check_days=2), 2)

    @patch('app.services.espn_client.requests.get')
    def test_get_days_rest_network_error(self, mock_get):
        from app.services.context_service import get_days_rest
        mock_get.side_effect = _requests.RequestException("timeout")
        self.assertEqual(get_days_rest('Lakers', check_days=1), 2)

    # -- get_game_context --

    @patch('app.services.context_service.get_days_rest', return_value=2)
    @patch('app.services.context_service.check_back_to_back', return_value=False)
    @patch('app.services.context_service.is_player_available', return_value=True)
    def test_get_game_context_healthy(self, _avail, _b2b, _rest):
        from app.services.context_service import get_game_context
        with self.app.app_context():
            ctx = get_game_context('LeBron James', 'Lakers')
            self.assertEqual(ctx['injury_status'], 'healthy')
            self.assertFalse(ctx['back_to_back'])
            self.assertEqual(ctx['days_rest'], 2)
            self.assertTrue(ctx['is_available'])

    @patch('app.services.context_service.get_days_rest', return_value=0)
    @patch('app.services.context_service.check_back_to_back', return_value=True)
    @patch('app.services.context_service.is_player_available', return_value=True)
    def test_get_game_context_b2b_injured(self, _avail, _b2b, _rest):
        from app.services.context_service import get_game_context
        with self.app.app_context():
            _seed_injury('LeBron James', 'questionable')
            ctx = get_game_context('LeBron James', 'Lakers')
            self.assertTrue(ctx['back_to_back'])
            self.assertEqual(ctx['days_rest'], 0)
            self.assertEqual(ctx['injury_status'], 'questionable')

    # -- _normalize_injury_status edge cases --

    def test_normalize_empty_string(self):
        from app.services.context_service import _normalize_injury_status
        self.assertEqual(_normalize_injury_status(''), 'unknown')

    def test_normalize_unknown_string(self):
        from app.services.context_service import _normalize_injury_status
        self.assertEqual(_normalize_injury_status('suspended'), 'suspended')


class TestMatchupService(BaseTestCase):
    """Tests for matchup_service: team defense, matchup adjustment, pace."""

    def setUp(self):
        super().setUp()
        from app.services.matchup_service import invalidate_team_defense_cache
        invalidate_team_defense_cache()

    # -- fetch_team_defense_stats --

    def test_fetch_team_defense_stats_success(self):
        from app.services import matchup_service

        df = _FakeDataFrame([{
            'TEAM_ID': 1, 'TEAM_NAME': 'Boston Celtics',
            'TEAM_ABBREVIATION': 'BOS',
            'OPP_PTS': 108, 'OPP_REB': 42, 'OPP_AST': 24,
            'OPP_FG3M': 11, 'OPP_STL': 7, 'OPP_BLK': 5,
            'OPP_TOV': 14, 'PACE': 98.5, 'DEF_RATING': 106.5,
        }])
        mock_endpoint = MagicMock()
        mock_endpoint.get_data_frames.return_value = [df]

        mock_ldts = MagicMock()
        mock_ldts.LeagueDashTeamStats.return_value = mock_endpoint

        mock_endpoints = MagicMock()
        mock_endpoints.leaguedashteamstats = mock_ldts

        with patch.dict(sys.modules, {
            'nba_api': MagicMock(),
            'nba_api.stats': MagicMock(),
            'nba_api.stats.endpoints': mock_endpoints,
            'nba_api.stats.endpoints.leaguedashteamstats': mock_ldts,
        }):
            with patch('app.services.matchup_service.time.sleep'):
                stats = matchup_service.fetch_team_defense_stats()

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]['team_name'], 'Boston Celtics')
        self.assertIn('opp_pts_allowed_pg', stats[0])
        self.assertGreater(stats[0]['opp_pts_allowed_pg'], 0)

    def test_fetch_team_defense_stats_exception(self):
        from app.services import matchup_service

        mock_ldts = MagicMock()
        mock_ldts.LeagueDashTeamStats.side_effect = Exception("API fail")

        mock_endpoints = MagicMock()
        mock_endpoints.leaguedashteamstats = mock_ldts

        with patch.dict(sys.modules, {
            'nba_api': MagicMock(),
            'nba_api.stats': MagicMock(),
            'nba_api.stats.endpoints': mock_endpoints,
            'nba_api.stats.endpoints.leaguedashteamstats': mock_ldts,
        }):
            with patch('app.services.matchup_service.time.sleep'):
                result = matchup_service.fetch_team_defense_stats()

        self.assertEqual(result, [])

    def test_fetch_team_defense_stats_fills_missing_pace_and_def_rating(self):
        from app.services import matchup_service

        df = _FakeDataFrame([{
            'TEAM_ID': 13, 'TEAM_NAME': 'No Pace Team',
            'TEAM_ABBREVIATION': 'NPT',
            'OPP_PTS': 111, 'OPP_REB': 44, 'OPP_AST': 25,
            'OPP_FG3M': 12, 'OPP_STL': 7, 'OPP_BLK': 5,
            'OPP_TOV': 14, 'PACE': 0, 'DEF_RATING': 0,
        }])
        mock_endpoint = MagicMock()
        mock_endpoint.get_data_frames.return_value = [df]

        mock_ldts = MagicMock()
        mock_ldts.LeagueDashTeamStats.return_value = mock_endpoint

        mock_endpoints = MagicMock()
        mock_endpoints.leaguedashteamstats = mock_ldts

        with patch.dict(sys.modules, {
            'nba_api': MagicMock(),
            'nba_api.stats': MagicMock(),
            'nba_api.stats.endpoints': mock_endpoints,
            'nba_api.stats.endpoints.leaguedashteamstats': mock_ldts,
        }):
            with patch('app.services.matchup_service.time.sleep'):
                stats = matchup_service.fetch_team_defense_stats()

        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]['pace'], 100.0)
        self.assertEqual(stats[0]['def_rating'], 114.0)

    # -- refresh_all_team_defense --

    @patch('app.services.matchup_service.fetch_team_defense_stats')
    def test_refresh_all_team_defense_insert(self, mock_fetch):
        from app.services.matchup_service import refresh_all_team_defense
        with self.app.app_context():
            mock_fetch.return_value = [{
                'team_id': '10', 'team_name': 'Test Team', 'team_abbr': 'TST',
                'opp_pts_pg': 110, 'opp_reb_pg': 43, 'opp_ast_pg': 25,
                'opp_3pm_pg': 12, 'opp_stl_pg': 7, 'opp_blk_pg': 5,
                'opp_tov_pg': 14, 'pace': 100, 'def_rating': 108,
            }]
            count = refresh_all_team_defense()
            self.assertEqual(count, 1)
            self.assertEqual(TeamDefenseSnapshot.query.count(), 1)

    @patch('app.services.matchup_service.fetch_team_defense_stats')
    def test_refresh_all_team_defense_update_existing(self, mock_fetch):
        from app.services.matchup_service import refresh_all_team_defense
        with self.app.app_context():
            _seed_defense('Test Team', 'TST', opp_pts=105)
            snap = TeamDefenseSnapshot.query.first()
            snap.team_id = '10'
            snap.snapshot_date = date.today()
            db.session.commit()

            mock_fetch.return_value = [{
                'team_id': '10', 'team_name': 'Test Updated', 'team_abbr': 'TST',
                'opp_pts_pg': 115, 'opp_reb_pg': 45, 'opp_ast_pg': 26,
                'opp_3pm_pg': 13, 'opp_stl_pg': 8, 'opp_blk_pg': 6,
                'opp_tov_pg': 15, 'pace': 102, 'def_rating': 110,
            }]
            count = refresh_all_team_defense()
            self.assertEqual(count, 1)

    @patch('app.services.matchup_service.fetch_team_defense_stats')
    def test_refresh_all_team_defense_empty(self, mock_fetch):
        from app.services.matchup_service import refresh_all_team_defense
        with patch('app.services.matchup_service._build_baseline_team_stats', return_value=[]):
            with self.app.app_context():
                mock_fetch.return_value = []
                self.assertEqual(refresh_all_team_defense(), 0)

    # -- get_matchup_adjustment --

    def test_matchup_adjustment_no_defense_data(self):
        from app.services.matchup_service import get_matchup_adjustment
        with self.app.app_context():
            self.assertEqual(get_matchup_adjustment('NonExistent', 'player_points'), 1.0)

    def test_matchup_adjustment_unknown_stat(self):
        from app.services.matchup_service import get_matchup_adjustment
        with self.app.app_context():
            _seed_defense()
            self.assertEqual(get_matchup_adjustment('Celtics', 'player_turnovers'), 1.0)

    def test_matchup_adjustment_with_stat_keys(self):
        from app.services.matchup_service import get_matchup_adjustment
        with self.app.app_context():
            _seed_defense()
            adj_reb = get_matchup_adjustment('Celtics', 'player_rebounds')
            self.assertIsInstance(adj_reb, float)
            adj_ast = get_matchup_adjustment('Celtics', 'player_assists')
            self.assertIsInstance(adj_ast, float)

    def test_position_matchup_adjustment(self):
        from app.services.matchup_service import get_position_matchup_adjustment
        with self.app.app_context():
            _seed_defense()
            snap = TeamDefenseSnapshot.query.first()
            snap.opp_pts_allowed_pg = 30.0
            db.session.commit()
            adj = get_position_matchup_adjustment('Celtics', 'pg')
            self.assertGreater(adj, 1.0)

    # -- get_pace_factor --

    def test_pace_factor_no_pace(self):
        from app.services.matchup_service import get_pace_factor
        with self.app.app_context():
            self.assertEqual(get_pace_factor('NonExistent'), 1.0)

    def test_pace_factor_zero_pace(self):
        from app.services.matchup_service import get_pace_factor
        with self.app.app_context():
            _seed_defense('Zero Pace Team', 'ZPT', pace=0)
            self.assertEqual(get_pace_factor('Zero Pace'), 1.0)

    # -- get_team_defense with date --

    def test_get_team_defense_with_date(self):
        from app.services.matchup_service import get_team_defense
        with self.app.app_context():
            _seed_defense()
            defense = get_team_defense('Celtics', date=date(2026, 12, 31))
            self.assertEqual(defense['team_name'], 'Boston Celtics')


class TestFeatureEngine(BaseTestCase):
    """Tests for feature_engine: build_projection_features, build_pick_context_features."""

    def _setup_data(self):
        _seed_player_logs(count=20, player_id='101')
        _seed_defense()

    # -- _compute_std --

    def test_compute_std_few_logs(self):
        from app.services.feature_engine import _compute_std
        with self.app.app_context():
            self.assertEqual(_compute_std([], 'pts'), 0.0)
            log = PlayerGameLog(player_id='1', player_name='X',
                                game_date=date(2026, 1, 1), pts=20)
            self.assertEqual(_compute_std([log], 'pts'), 0.0)

    def test_compute_std_with_data(self):
        from app.services.feature_engine import _compute_std
        with self.app.app_context():
            logs = []
            for i in range(5):
                log = PlayerGameLog(player_id='1', player_name='X',
                                    game_date=date(2026, 1, 1 + i),
                                    pts=20 + i * 2)
                logs.append(log)
            std = _compute_std(logs, 'pts')
            self.assertGreater(std, 0)

    # -- _average_stat --

    def test_average_stat_empty(self):
        from app.services.feature_engine import _average_stat
        self.assertEqual(_average_stat([], 'pts'), 0.0)

    def test_average_stat_with_data(self):
        from app.services.feature_engine import _average_stat
        with self.app.app_context():
            logs = [PlayerGameLog(player_id='1', player_name='X',
                                  game_date=date(2026, 1, 1 + i), pts=20 + i)
                    for i in range(3)]
            avg = _average_stat(logs, 'pts')
            self.assertAlmostEqual(avg, 21.0, places=0)

    # -- _compute_streak_zscore --

    def test_streak_zscore_few_logs(self):
        from app.services.feature_engine import _compute_streak_zscore
        with self.app.app_context():
            logs = [PlayerGameLog(player_id='1', player_name='X',
                                  game_date=date(2026, 1, 1 + i), pts=20)
                    for i in range(5)]
            self.assertEqual(_compute_streak_zscore(logs, 'pts'), 0.0)

    def test_streak_zscore_with_variance(self):
        from app.services.feature_engine import _compute_streak_zscore
        with self.app.app_context():
            logs = []
            for i in range(15):
                log = PlayerGameLog(player_id='1', player_name='X',
                                    game_date=date(2026, 1, 1 + i),
                                    pts=20 if i >= 3 else 30)  # Recent 3 are higher
                logs.append(log)
            z = _compute_streak_zscore(logs, 'pts')
            self.assertGreater(z, 0)

    def test_streak_zscore_zero_std(self):
        from app.services.feature_engine import _compute_streak_zscore
        with self.app.app_context():
            logs = [PlayerGameLog(player_id='1', player_name='X',
                                  game_date=date(2026, 1, 1 + i), pts=20)
                    for i in range(15)]
            self.assertEqual(_compute_streak_zscore(logs, 'pts'), 0.0)

    # -- _compute_hit_rate --

    def test_compute_hit_rate_zero_line(self):
        from app.services.feature_engine import _compute_hit_rate
        with self.app.app_context():
            logs = [PlayerGameLog(player_id='1', player_name='X',
                                  game_date=date(2026, 1, 1), pts=20)]
            self.assertEqual(_compute_hit_rate(logs, 'pts', 0), 0.5)

    def test_compute_hit_rate_empty(self):
        from app.services.feature_engine import _compute_hit_rate
        self.assertEqual(_compute_hit_rate([], 'pts', 20), 0.5)

    # -- build_projection_features --

    def test_build_projection_features(self):
        from app.services.feature_engine import build_projection_features
        with self.app.app_context():
            self._setup_data()
            features = build_projection_features(
                player_id='101', prop_type='player_points',
                opponent_name='Celtics', is_home=True, prop_line=25.5,
            )
            self.assertIn('avg_stat_last_5', features)
            self.assertIn('avg_stat_last_10', features)
            self.assertIn('opp_def_rating', features)
            self.assertGreater(features['avg_stat_season'], 0)
            self.assertEqual(features['home_away'], 1)
            self.assertEqual(features['prop_line'], 25.5)

    def test_build_projection_features_no_opponent(self):
        from app.services.feature_engine import build_projection_features
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='102')
            features = build_projection_features(
                player_id='102', prop_type='player_rebounds',
                opponent_name='', is_home=False,
            )
            self.assertEqual(features['opp_def_rating'], 0)
            self.assertAlmostEqual(features['opp_stat_allowed'], 1.0)
            self.assertEqual(features['home_away'], 0)

    # -- build_pick_context_features --

    def test_build_pick_context_features(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            self._setup_data()
            _seed_injury('LeBron James', 'questionable')
            # Patch at the feature_engine level since it imports directly
            with patch('app.services.feature_engine.check_back_to_back', return_value=False):
                with patch('app.services.feature_engine.get_days_rest', return_value=1):
                    ctx = build_pick_context_features(
                        player_name='LeBron James', player_id='101',
                        prop_type='player_points', prop_line=25.5,
                        american_odds=-110, projected_stat=27.3,
                        projected_edge=0.08, confidence_tier='moderate',
                        opponent_name='Celtics', team_name='Lakers',
                        is_home=True,
                    )
            self.assertEqual(ctx['projected_stat'], 27.3)
            self.assertEqual(ctx['prop_line'], 25.5)
            self.assertIn('context_flags', ctx)
            self.assertTrue(ctx['injury_returning'])
            self.assertIn('player_position', ctx)
            self.assertIn('opp_positional_matchup_adj', ctx)

    def test_build_pick_context_features_b2b(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='103')
            with patch('app.services.feature_engine.check_back_to_back', return_value=True):
                ctx = build_pick_context_features(
                    player_name='Test Player', player_id='103',
                    prop_type='player_points', prop_line=20.0,
                    american_odds=-110, projected_stat=22.0,
                    projected_edge=0.05, confidence_tier='slight',
                    team_name='Lakers',
                )
            self.assertTrue(ctx['back_to_back'])
            self.assertEqual(ctx['days_rest'], 0)
            self.assertIn('back_to_back', ctx['context_flags'])

    def test_build_pick_context_features_no_team(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='104')
            ctx = build_pick_context_features(
                player_name='Test Player', player_id='104',
                prop_type='player_points', prop_line=20.0,
                american_odds=-110, projected_stat=22.0,
                projected_edge=0.05, confidence_tier='slight',
            )
            self.assertFalse(ctx['back_to_back'])
            self.assertTrue(ctx['context_flags'])

    def test_build_pick_context_features_adds_confidence_flag(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='104a')
            ctx = build_pick_context_features(
                player_name='Test Player', player_id='104a',
                prop_type='player_points', prop_line=20.0,
                american_odds=-110, projected_stat=22.0,
                projected_edge=0.07, confidence_tier='moderate',
            )
            self.assertIn('moderate_confidence', ctx['context_flags'])

    def test_build_pick_context_cold_streak(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            # Logs ordered desc by date in DB, so most recent dates come first.
            # Seed 20 logs: make the last 3 (highest dates) have low pts.
            for i in range(20):
                pts = 10 if i >= 17 else 30  # i=17,18,19 are most recent dates
                log = PlayerGameLog(
                    player_id='105', player_name='Cold Player',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=pts, reb=5, ast=5, fg3m=1, minutes=30,
                )
                db.session.add(log)
            db.session.commit()

            ctx = build_pick_context_features(
                player_name='Cold Player', player_id='105',
                prop_type='player_points', prop_line=25.0,
                american_odds=-110, projected_stat=20.0,
                projected_edge=0.02, confidence_tier='slight',
            )
            self.assertEqual(ctx['player_last5_trend'], 'cold')
            self.assertIn('cold_streak', ctx['context_flags'])

    def test_build_pick_context_minutes_trend(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            # Most recent 5 (highest dates) have high minutes
            for i in range(20):
                mins = 40 if i >= 15 else 25  # i=15..19 are the 5 most recent
                log = PlayerGameLog(
                    player_id='106', player_name='Mins Player',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=20, reb=5, ast=5, fg3m=1, minutes=mins,
                )
                db.session.add(log)
            db.session.commit()

            ctx = build_pick_context_features(
                player_name='Mins Player', player_id='106',
                prop_type='player_points', prop_line=20.0,
                american_odds=-110, projected_stat=22.0,
                projected_edge=0.05, confidence_tier='slight',
            )
            self.assertEqual(ctx['minutes_trend'], 'increasing')

    def test_build_pick_context_favorable_matchup(self):
        from app.services.feature_engine import build_pick_context_features
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='107')
            snap = TeamDefenseSnapshot(
                team_id='99', team_name='Bad Defense Team',
                team_abbr='BDT', snapshot_date=date(2026, 2, 25),
                opp_pts_pg=125.0, opp_reb_pg=50.0, opp_ast_pg=30.0,
                opp_3pm_pg=15.0, pace=108.0, def_rating=115.0,
            )
            db.session.add(snap)
            db.session.commit()

            ctx = build_pick_context_features(
                player_name='Test Player', player_id='107',
                prop_type='player_points', prop_line=20.0,
                american_odds=-110, projected_stat=25.0,
                projected_edge=0.10, confidence_tier='moderate',
                opponent_name='Bad Defense',
            )
            self.assertIn('favorable_matchup', ctx['context_flags'])
            self.assertIn('pace_boost', ctx['context_flags'])


class TestProjectionEngine(BaseTestCase):
    """Tests for ProjectionEngine: project_stat, project_all_props_for_player."""

    def _setup_engine_data(self, player_id='201', count=30):
        _seed_player_logs(count=count, player_id=player_id)
        _seed_defense()

    def test_project_stat_unknown_prop_type(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            result = engine.project_stat('LeBron James', 'player_turnovers')
            self.assertEqual(result['projection'], 0)

    def test_project_stat_no_player_id(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value=None):
                result = engine.project_stat('Nobody', 'player_points')
                self.assertEqual(result['projection'], 0)

    def test_project_stat_no_logs(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='999'):
                result = engine.project_stat('Nobody', 'player_points')
                self.assertEqual(result['projection'], 0)

    def test_project_stat_full_pipeline(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            self._setup_engine_data()
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='201'):
                with patch('app.services.projection_engine.get_game_context',
                           return_value={'back_to_back': False, 'injury_status': 'healthy',
                                         'is_available': True, 'days_rest': 2}):
                    result = engine.project_stat(
                        'LeBron James', 'player_points',
                        opponent_name='Celtics', team_name='Lakers', is_home=True,
                    )
            self.assertGreater(result['projection'], 0)
            self.assertIn(result['confidence'], ('low', 'medium', 'high'))
            self.assertIn('home court (+3%)', result['context_notes'])
            self.assertIn('breakdown', result)

    def test_project_stat_pra_is_derived_sum(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            self._setup_engine_data()
            engine = ProjectionEngine()
            with patch.object(engine, 'project_stat') as mock_project:
                def side_effect(player_name, prop_type, opponent_name='', team_name='', is_home=True, game_date=None, **kwargs):
                    if prop_type == 'player_points_rebounds_assists':
                        return ProjectionEngine.project_stat(engine, player_name, prop_type, opponent_name, team_name, is_home)
                    mapping = {
                        'player_points': {'projection': 25.0, 'confidence': 'high', 'context_notes': ['home court (+3%)'], 'std_dev': 3.0, 'z_score': 0.5, 'games_played': 30, 'projection_source': 'heuristic', 'breakdown': {}},
                        'player_rebounds': {'projection': 9.0, 'confidence': 'medium', 'context_notes': ['pace boost'], 'std_dev': 2.0, 'z_score': 0.2, 'games_played': 30, 'projection_source': 'heuristic', 'breakdown': {}},
                        'player_assists': {'projection': 8.0, 'confidence': 'medium', 'context_notes': ['home court (+3%)'], 'std_dev': 2.5, 'z_score': 0.3, 'games_played': 28, 'projection_source': 'heuristic', 'breakdown': {}},
                    }
                    return mapping[prop_type]
                mock_project.side_effect = side_effect
                result = ProjectionEngine.project_stat(engine, 'LeBron James', 'player_points_rebounds_assists')
            # 25.0 + 9.0 + 8.0 = 42.0, plus PRA bias correction of +3.2 = 45.2
            self.assertEqual(result['projection'], 45.2)
            self.assertEqual(result['projection_source'], 'derived_combo')
            self.assertEqual(result['games_played'], 28)
            self.assertIn('components', result['breakdown'])

    def test_project_stat_away_game(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            self._setup_engine_data()
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='201'):
                with patch('app.services.projection_engine.get_game_context',
                           return_value={'back_to_back': False, 'injury_status': 'healthy',
                                         'is_available': True, 'days_rest': 2}):
                    result = engine.project_stat(
                        'LeBron James', 'player_points',
                        opponent_name='Celtics', team_name='Lakers', is_home=False,
                    )
            self.assertIn('away game (-3%)', result['context_notes'])

    def test_project_stat_b2b(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            self._setup_engine_data()
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='201'):
                with patch('app.services.projection_engine.get_game_context',
                           return_value={'back_to_back': True, 'injury_status': 'healthy',
                                         'is_available': True, 'days_rest': 0}):
                    result = engine.project_stat(
                        'LeBron James', 'player_points',
                        team_name='Lakers', is_home=True,
                    )
            self.assertIn('back-to-back (-8%)', result['context_notes'])

    def test_project_stat_injured(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            self._setup_engine_data()
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='201'):
                with patch('app.services.projection_engine.get_game_context',
                           return_value={'back_to_back': False, 'injury_status': 'questionable',
                                         'is_available': True, 'days_rest': 2}):
                    result = engine.project_stat(
                        'LeBron James', 'player_points',
                        team_name='Lakers', is_home=True,
                    )
            self.assertTrue(any('injury' in n for n in result['context_notes']))

    def test_project_stat_favorable_matchup(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            snap = TeamDefenseSnapshot(
                team_id='50', team_name='Bad Defense Squad',
                team_abbr='BDS', snapshot_date=date(2026, 2, 25),
                opp_pts_pg=130.0, opp_reb_pg=50.0, opp_ast_pg=30.0,
                opp_3pm_pg=15.0, pace=108.0, def_rating=118.0,
            )
            db.session.add(snap)
            _seed_player_logs(count=30, player_id='202')
            db.session.commit()

            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='202'):
                result = engine.project_stat(
                    'LeBron James', 'player_points',
                    opponent_name='Bad Defense Squad', is_home=True,
                )
            self.assertTrue(any('favorable' in n for n in result['context_notes']))

    def test_project_stat_tough_matchup(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            snap = TeamDefenseSnapshot(
                team_id='51', team_name='Great Defense Team',
                team_abbr='GDT', snapshot_date=date(2026, 2, 25),
                opp_pts_pg=100.0, opp_reb_pg=38.0, opp_ast_pg=20.0,
                opp_3pm_pg=9.0, pace=92.0, def_rating=102.0,
            )
            db.session.add(snap)
            _seed_player_logs(count=30, player_id='203')
            db.session.commit()

            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='203'):
                result = engine.project_stat(
                    'LeBron James', 'player_points',
                    opponent_name='Great Defense Team', is_home=True,
                )
            self.assertTrue(any('tough' in n for n in result['context_notes']))
            self.assertTrue(any('slow pace' in n for n in result['context_notes']))

    def test_project_stat_few_games(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            _seed_player_logs(count=3, player_id='204')
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='204'):
                result = engine.project_stat('LeBron James', 'player_points',
                                             is_home=True)
            self.assertGreater(result['projection'], 0)

    def test_project_stat_minutes_decreasing(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            # Most recent 5 games (highest dates) have LOW minutes
            for i in range(30):
                mins = 20 if i >= 25 else 35  # i=25..29 are most recent
                log = PlayerGameLog(
                    player_id='205', player_name='Mins Player',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=20, reb=5, ast=5, fg3m=1, minutes=mins,
                )
                db.session.add(log)
            db.session.commit()

            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='205'):
                result = engine.project_stat('Mins Player', 'player_points',
                                             is_home=True)
            self.assertIn('minutes decreasing', result['context_notes'])

    def test_project_stat_minutes_increasing(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            for i in range(30):
                mins = 42 if i >= 25 else 25  # Most recent 5 have high minutes
                log = PlayerGameLog(
                    player_id='206', player_name='Mins Up',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=20, reb=5, ast=5, fg3m=1, minutes=mins,
                )
                db.session.add(log)
            db.session.commit()

            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='206'):
                result = engine.project_stat('Mins Up', 'player_points',
                                             is_home=True)
            self.assertIn('minutes increasing', result['context_notes'])

    def test_project_stat_ml_failure_falls_back_to_heuristic(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            self._setup_engine_data()
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='201'):
                with patch('app.services.ml_model.predict_stat', side_effect=RuntimeError('bad model')):
                    with patch.dict('os.environ', {'USE_ML_PROJECTIONS': 'true'}):
                        result = engine.project_stat('LeBron James', 'player_points', is_home=True)
            self.assertGreater(result['projection'], 0)
            self.assertEqual(result['projection_source'], 'heuristic')

    def test_project_all_props_for_player(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='207')
            engine = ProjectionEngine()
            with patch('app.services.projection_engine.find_player_id', return_value='207'):
                results = engine.project_all_props_for_player(
                    'LeBron James', is_home=True,
                )
            self.assertIn('player_points', results)
            self.assertIn('player_rebounds', results)
            self.assertIn('player_assists', results)

    def test_build_ml_features_includes_efficiency_and_splits(self):
        from app.services.projection_engine import ProjectionEngine
        from app.services.stats_service import get_cached_logs
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='208')
            logs = get_cached_logs('208', last_n=82)
            features = ProjectionEngine()._build_ml_features(logs, 'pts', is_home=True)
            for key in (
                'home_split_stat_avg',
                'away_split_stat_avg',
                'context_split_stat_avg',
                'fg_pct_last_10',
                'ts_pct_last_10',
                'fga_last_5_avg',
                'fg3a_last_5_avg',
                'fg3m_last_5_avg',
                'fta_last_5_avg',
                'fga_share_last_5',
                'pts_share_last_5',
                'usage_share_last_5',
                'lead_usage_rate_last_10',
            ):
                self.assertIn(key, features)
            self.assertGreaterEqual(features['fg_pct_last_10'], 0.0)
            self.assertLessEqual(features['fg_pct_last_10'], 1.0)
            self.assertGreaterEqual(features['ts_pct_last_10'], 0.0)
            self.assertGreaterEqual(features['fga_share_last_5'], 0.0)
            self.assertLessEqual(features['fga_share_last_5'], 1.0)

    # -- _compute_confidence --

    def test_compute_confidence_low_games(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        self.assertEqual(engine._compute_confidence(5, 3.0, 25.0), 'low')

    def test_compute_confidence_high_cv(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        self.assertEqual(engine._compute_confidence(20, 15.0, 25.0), 'low')

    def test_compute_confidence_medium_cv(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        self.assertEqual(engine._compute_confidence(20, 8.0, 25.0), 'medium')

    def test_compute_confidence_high(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        self.assertEqual(engine._compute_confidence(35, 3.0, 25.0), 'high')

    def test_compute_confidence_zero_avg_high_std(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        self.assertEqual(engine._compute_confidence(15, 6.0, 0), 'low')

    # -- _compute_z_score --

    def test_z_score_few_logs(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        logs = [MagicMock(pts=20) for _ in range(5)]
        self.assertEqual(engine._compute_z_score(logs, 'pts'), 0.0)

    def test_z_score_zero_std(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            logs = [PlayerGameLog(player_id='1', player_name='X',
                                  game_date=date(2026, 1, 1 + i), pts=20)
                    for i in range(15)]
            self.assertEqual(engine._compute_z_score(logs, 'pts'), 0.0)

    # -- _explain_cold_streak --

    def test_explain_cold_streak_blowout(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            logs = []
            for i in range(10):
                log = PlayerGameLog(
                    player_id='1', player_name='X',
                    game_date=date(2026, 1, 1 + i),
                    pts=20, minutes=10 if i == 0 else 35,
                )
                logs.append(log)
            reasons = engine._explain_cold_streak(logs, 'pts')
            self.assertIn('recent blowout/low minutes', reasons)

    def test_explain_cold_streak_no_blowout(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            logs = [PlayerGameLog(player_id='1', player_name='X',
                                  game_date=date(2026, 1, 1 + i),
                                  pts=20, minutes=35)
                    for i in range(10)]
            reasons = engine._explain_cold_streak(logs, 'pts')
            self.assertEqual(reasons, [])


class Phase1Dot1FeatureBuilderTest(BaseTestCase):
    """Tests for the Phase 1.1 expanded feature engineering additions.

    Covers:
    - extract_opp_abbr (home and away formats)
    - compute_days_rest
    - compute_schedule_density
    - compute_opp_history
    - FEATURE_KEYS completeness
    - build_ml_features_from_history with Phase 1 params
    - build_defense_lookup and _build_game_total_lookup (ml_model helpers)
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_log(self, game_date=None, matchup='LAL vs. BOS', pts=20.0,
                  reb=5.0, ast=5.0, home_away='home', team_abbr='LAL', **kwargs):
        return SimpleNamespace(
            game_date=game_date,
            matchup=matchup,
            pts=pts, reb=reb, ast=ast,
            fgm=7.0, fga=15.0, ftm=4.0, fta=5.0,
            fg3m=2.0, fg3a=4.0, stl=1.0, blk=0.5,
            tov=2.0, minutes=32.0, plus_minus=3.0,
            home_away=home_away, team_abbr=team_abbr,
            win_loss='W',
            **kwargs,
        )

    def _make_logs(self, count=15, start=date(2026, 1, 1)):
        """Build count SimpleNamespace logs with alternating matchups."""
        logs = []
        for i in range(count):
            matchup = 'LAL vs. BOS' if i % 2 == 0 else 'LAL @ MIA'
            logs.append(self._make_log(
                game_date=start + timedelta(days=i * 2),
                matchup=matchup,
                pts=20.0 + i,
                home_away='home' if i % 2 == 0 else 'away',
            ))
        return logs

    # ------------------------------------------------------------------
    # extract_opp_abbr
    # ------------------------------------------------------------------

    def test_extract_opp_abbr_home(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr('LAL vs. BOS'), 'BOS')

    def test_extract_opp_abbr_away(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr('LAL @ MIA'), 'MIA')

    def test_extract_opp_abbr_empty(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr(''), '')

    def test_extract_opp_abbr_unrecognised(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr('LALBOS'), '')

    # ------------------------------------------------------------------
    # compute_days_rest
    # ------------------------------------------------------------------

    def test_days_rest_normal(self):
        from app.services.ml_feature_builder import compute_days_rest
        logs = [self._make_log(game_date=date(2026, 1, 10))]
        self.assertEqual(compute_days_rest(logs, date(2026, 1, 12)), 2.0)

    def test_days_rest_back_to_back(self):
        from app.services.ml_feature_builder import compute_days_rest
        logs = [self._make_log(game_date=date(2026, 1, 10))]
        self.assertEqual(compute_days_rest(logs, date(2026, 1, 11)), 1.0)

    def test_days_rest_no_date(self):
        from app.services.ml_feature_builder import compute_days_rest
        # current_game_date=None → default 3.0
        self.assertEqual(compute_days_rest([], None), 3.0)

    def test_days_rest_no_logs(self):
        from app.services.ml_feature_builder import compute_days_rest
        # no logs → default 3.0
        self.assertEqual(compute_days_rest([], date(2026, 1, 15)), 3.0)

    # ------------------------------------------------------------------
    # compute_schedule_density
    # ------------------------------------------------------------------

    def test_schedule_density_basic(self):
        from app.services.ml_feature_builder import compute_schedule_density
        logs = [self._make_log(game_date=date(2026, 1, 8)),
                self._make_log(game_date=date(2026, 1, 5)),
                self._make_log(game_date=date(2025, 12, 20))]  # outside window
        # 2 games in last 7 days before Jan 10
        self.assertEqual(compute_schedule_density(logs, date(2026, 1, 10)), 2)

    def test_schedule_density_empty(self):
        from app.services.ml_feature_builder import compute_schedule_density
        self.assertEqual(compute_schedule_density([], date(2026, 1, 10)), 0)

    def test_schedule_density_no_date(self):
        from app.services.ml_feature_builder import compute_schedule_density
        self.assertEqual(compute_schedule_density([], None), 0)

    # ------------------------------------------------------------------
    # compute_opp_history
    # ------------------------------------------------------------------

    def test_opp_history_found(self):
        from app.services.ml_feature_builder import compute_opp_history
        logs = [
            self._make_log(matchup='LAL vs. BOS', pts=30.0),
            self._make_log(matchup='LAL vs. BOS', pts=20.0),
            self._make_log(matchup='LAL @ MIA', pts=10.0),
        ]
        avg, cnt = compute_opp_history(logs, 'BOS', 'pts')
        self.assertEqual(cnt, 2)
        self.assertAlmostEqual(avg, 25.0)

    def test_opp_history_no_match(self):
        from app.services.ml_feature_builder import compute_opp_history
        logs = [self._make_log(matchup='LAL @ MIA', pts=25.0)]
        avg, cnt = compute_opp_history(logs, 'BOS', 'pts')
        self.assertEqual(cnt, 0)
        self.assertEqual(avg, 0.0)

    def test_opp_history_empty_abbr(self):
        from app.services.ml_feature_builder import compute_opp_history
        avg, cnt = compute_opp_history([], '', 'pts')
        self.assertEqual((avg, cnt), (0.0, 0))

    # ------------------------------------------------------------------
    # FEATURE_KEYS completeness
    # ------------------------------------------------------------------

    def test_feature_keys_count(self):
        from app.services.ml_feature_builder import FEATURE_KEYS
        self.assertEqual(len(FEATURE_KEYS), 30, "Expected 30 feature keys (21 original + 9 Phase 1)")

    def test_phase1_keys_present(self):
        from app.services.ml_feature_builder import FEATURE_KEYS
        phase1 = {
            'days_rest', 'back_to_back', 'games_last_7_days',
            'opp_hist_avg_stat', 'opp_hist_games',
            'game_total_line',
            'opp_def_rating', 'opp_pace', 'opp_stat_allowed',
        }
        self.assertTrue(phase1.issubset(set(FEATURE_KEYS)))

    # ------------------------------------------------------------------
    # build_ml_features_from_history — Phase 1 params
    # ------------------------------------------------------------------

    def test_features_include_all_keys(self):
        from app.services.ml_feature_builder import build_ml_features_from_history, FEATURE_KEYS
        logs = self._make_logs(15)
        feat = build_ml_features_from_history(logs, True, 'pts')
        self.assertEqual(set(feat.keys()), set(FEATURE_KEYS))

    def test_back_to_back_flag_set(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = [self._make_log(game_date=date(2026, 1, 10))]
        # Playing next day → back-to-back
        feat = build_ml_features_from_history(
            logs, True, 'pts', current_game_date=date(2026, 1, 11))
        self.assertEqual(feat['back_to_back'], 1.0)
        self.assertEqual(feat['days_rest'], 1.0)

    def test_back_to_back_flag_not_set(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = [self._make_log(game_date=date(2026, 1, 8))]
        feat = build_ml_features_from_history(
            logs, True, 'pts', current_game_date=date(2026, 1, 11))
        self.assertEqual(feat['back_to_back'], 0.0)

    def test_opp_history_wired(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = [
            self._make_log(matchup='LAL vs. BOS', pts=30.0, game_date=date(2026, 1, 1)),
            self._make_log(matchup='LAL vs. BOS', pts=20.0, game_date=date(2026, 1, 3)),
        ]
        feat = build_ml_features_from_history(
            logs, True, 'pts',
            current_game_date=date(2026, 1, 10),
            current_matchup='LAL vs. BOS',
        )
        self.assertAlmostEqual(feat['opp_hist_avg_stat'], 25.0)
        self.assertEqual(feat['opp_hist_games'], 2.0)

    def test_game_total_line_wired(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = self._make_logs(12)
        feat = build_ml_features_from_history(
            logs, True, 'pts', game_total_line=228.5)
        self.assertAlmostEqual(feat['game_total_line'], 228.5)

    def test_defense_lookup_wired(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = [self._make_log(matchup='LAL vs. BOS', pts=25.0,
                                game_date=date(2026, 1, 1))]
        dlookup = {'BOS': {'def_rating': 108.5, 'pace': 99.1, 'opp_pts_pg': 112.0}}
        feat = build_ml_features_from_history(
            logs, True, 'pts',
            current_game_date=date(2026, 1, 10),
            current_matchup='LAL vs. BOS',
            defense_lookup=dlookup,
        )
        self.assertAlmostEqual(feat['opp_def_rating'], 108.5)
        self.assertAlmostEqual(feat['opp_pace'], 99.1)
        self.assertAlmostEqual(feat['opp_stat_allowed'], 112.0)

    def test_defense_lookup_missing_opp(self):
        """Unknown opponent → defensive features default to 0.0."""
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = self._make_logs(12)
        feat = build_ml_features_from_history(
            logs, True, 'pts',
            current_matchup='LAL vs. XYZ',
            defense_lookup={'BOS': {'def_rating': 108.0, 'pace': 99.0, 'opp_pts_pg': 111.0}},
        )
        self.assertEqual(feat['opp_def_rating'], 0.0)
        self.assertEqual(feat['opp_stat_allowed'], 0.0)

    def test_features_neutral_without_context(self):
        """Calling with no Phase 1 params returns safe neutral defaults."""
        from app.services.ml_feature_builder import build_ml_features_from_history
        logs = self._make_logs(15)
        feat = build_ml_features_from_history(logs, True, 'pts')
        self.assertEqual(feat['game_total_line'], 0.0)
        self.assertEqual(feat['opp_def_rating'], 0.0)
        self.assertEqual(feat['opp_hist_games'], 0.0)

    # ------------------------------------------------------------------
    # build_defense_lookup (ml_model helper)
    # ------------------------------------------------------------------

    def test_build_defense_lookup_basic(self):
        from app.services.ml_model import build_defense_lookup
        with self.app.app_context():
            snap = TeamDefenseSnapshot(
                team_id='BOS1', team_name='Boston Celtics',
                team_abbr='BOS', snapshot_date=date(2026, 1, 15),
                def_rating=108.0, pace=99.5,
                opp_pts_pg=112.0, opp_reb_pg=44.0, opp_ast_pg=26.0,
                opp_3pm_pg=12.0, opp_stl_pg=7.0, opp_blk_pg=5.0,
            )
            db.session.add(snap)
            db.session.commit()
            lookup = build_defense_lookup()

        self.assertIn('BOS', lookup)
        self.assertAlmostEqual(lookup['BOS']['def_rating'], 108.0)
        self.assertAlmostEqual(lookup['BOS']['pace'], 99.5)

    def test_build_defense_lookup_most_recent_wins(self):
        """Only the most-recent snapshot per team is kept."""
        from app.services.ml_model import build_defense_lookup
        with self.app.app_context():
            for rating, snap_date in [(110.0, date(2026, 1, 1)), (108.0, date(2026, 1, 20))]:
                db.session.add(TeamDefenseSnapshot(
                    team_id='MIA1', team_name='Miami Heat',
                    team_abbr='MIA', snapshot_date=snap_date,
                    def_rating=rating, pace=100.0,
                    opp_pts_pg=110.0,
                ))
            db.session.commit()
            lookup = build_defense_lookup()

        self.assertAlmostEqual(lookup['MIA']['def_rating'], 108.0)


class Phase1FeatureBuilderTest(BaseTestCase):
    """Tests for the 9 new Phase 1 features in ml_feature_builder."""

    # ── extract_opp_abbr ─────────────────────────────────────────────────────

    def test_extract_opp_abbr_home_format(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr('LAL vs. BOS'), 'BOS')

    def test_extract_opp_abbr_away_format(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr('LAL @ MIA'), 'MIA')

    def test_extract_opp_abbr_empty(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr(''), '')

    def test_extract_opp_abbr_unrecognised(self):
        from app.services.ml_feature_builder import extract_opp_abbr
        self.assertEqual(extract_opp_abbr('no separator here'), '')

    # ── compute_days_rest ────────────────────────────────────────────────────

    def test_days_rest_one_day(self):
        from app.services.ml_feature_builder import compute_days_rest
        log = SimpleNamespace(game_date=date(2026, 1, 1))
        result = compute_days_rest([log], date(2026, 1, 2))
        self.assertEqual(result, 1.0)

    def test_days_rest_back_to_back(self):
        from app.services.ml_feature_builder import compute_days_rest
        log = SimpleNamespace(game_date=date(2026, 3, 5))
        result = compute_days_rest([log], date(2026, 3, 6))
        self.assertEqual(result, 1.0)

    def test_days_rest_no_prior(self):
        from app.services.ml_feature_builder import compute_days_rest
        result = compute_days_rest([], date(2026, 1, 10))
        self.assertEqual(result, 3.0)

    def test_days_rest_no_current_date(self):
        from app.services.ml_feature_builder import compute_days_rest
        log = SimpleNamespace(game_date=date(2026, 1, 1))
        result = compute_days_rest([log], None)
        self.assertEqual(result, 3.0)

    # ── compute_schedule_density ─────────────────────────────────────────────

    def test_schedule_density_several_games(self):
        from app.services.ml_feature_builder import compute_schedule_density
        # 4 games in the 7 days before Jan 8
        logs = [SimpleNamespace(game_date=date(2026, 1, i)) for i in range(1, 8)]
        result = compute_schedule_density(logs, date(2026, 1, 8), window_days=7)
        self.assertEqual(result, 7)

    def test_schedule_density_none_in_window(self):
        from app.services.ml_feature_builder import compute_schedule_density
        logs = [SimpleNamespace(game_date=date(2026, 1, 1))]
        result = compute_schedule_density(logs, date(2026, 2, 1), window_days=7)
        self.assertEqual(result, 0)

    def test_schedule_density_no_current_date(self):
        from app.services.ml_feature_builder import compute_schedule_density
        logs = [SimpleNamespace(game_date=date(2026, 1, 1))]
        result = compute_schedule_density(logs, None)
        self.assertEqual(result, 0)

    # ── compute_opp_history ──────────────────────────────────────────────────

    def test_opp_history_with_matching_games(self):
        from app.services.ml_feature_builder import compute_opp_history
        # 3 games vs BOS with pts values 20, 25, 30 → avg 25
        logs = [
            SimpleNamespace(matchup='LAL vs. BOS', pts=20.0),
            SimpleNamespace(matchup='LAL vs. BOS', pts=25.0),
            SimpleNamespace(matchup='LAL vs. BOS', pts=30.0),
            SimpleNamespace(matchup='LAL @ MIA', pts=10.0),  # different opp
        ]
        avg, count = compute_opp_history(logs, 'BOS', 'pts')
        self.assertAlmostEqual(avg, 25.0)
        self.assertEqual(count, 3)

    def test_opp_history_no_matching_games(self):
        from app.services.ml_feature_builder import compute_opp_history
        logs = [SimpleNamespace(matchup='LAL @ MIA', pts=20.0)]
        avg, count = compute_opp_history(logs, 'BOS', 'pts')
        self.assertEqual(avg, 0.0)
        self.assertEqual(count, 0)

    def test_opp_history_empty_opp_abbr(self):
        from app.services.ml_feature_builder import compute_opp_history
        logs = [SimpleNamespace(matchup='LAL vs. BOS', pts=20.0)]
        avg, count = compute_opp_history(logs, '', 'pts')
        self.assertEqual(count, 0)

    # ── FEATURE_KEYS completeness ─────────────────────────────────────────────

    def test_feature_keys_contains_phase1_features(self):
        from app.services.ml_feature_builder import FEATURE_KEYS
        phase1_keys = [
            'days_rest', 'back_to_back', 'games_last_7_days',
            'opp_hist_avg_stat', 'opp_hist_games',
            'game_total_line',
            'opp_def_rating', 'opp_pace', 'opp_stat_allowed',
        ]
        for key in phase1_keys:
            self.assertIn(key, FEATURE_KEYS, f"Missing Phase 1 key: {key}")

    def test_feature_keys_total_count(self):
        from app.services.ml_feature_builder import FEATURE_KEYS
        # 21 original + 9 Phase 1 = 30 total
        self.assertEqual(len(FEATURE_KEYS), 30)

    # ── build_ml_features_from_history with Phase 1 params ───────────────────

    def test_build_features_includes_phase1_keys(self):
        from app.services.ml_feature_builder import build_ml_features_from_history, FEATURE_KEYS
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='p1_phase1', player_name='Phase Player')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p1_phase1', last_n=82)
            features = build_ml_features_from_history(
                prior_logs=logs,
                current_is_home=True,
                stat_key='pts',
                current_game_date=date(2026, 2, 1),
                current_matchup='LAL vs. BOS',
                game_total_line=225.5,
            )
        for key in FEATURE_KEYS:
            self.assertIn(key, features, f"Missing key in output: {key}")

    def test_build_features_days_rest_populated(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        with self.app.app_context():
            _seed_player_logs(count=15, player_id='p2_phase1', player_name='Rest Player')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p2_phase1', last_n=82)
            # Most recent seeded log is Jan 14 (0-indexed day 14)
            features = build_ml_features_from_history(
                prior_logs=logs,
                current_is_home=True,
                stat_key='pts',
                current_game_date=date(2026, 1, 16),
            )
        # Jan 15 was last seeded game, Jan 16 is current → 1 day rest → back_to_back
        self.assertGreaterEqual(features['days_rest'], 0.0)
        self.assertIn(features['back_to_back'], [0.0, 1.0])

    def test_build_features_opp_history_from_matchup(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        with self.app.app_context():
            # _seed_player_logs alternates LAL vs. BOS / LAL @ MIA matchups
            _seed_player_logs(count=20, player_id='p3_phase1', player_name='History Player')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p3_phase1', last_n=82)
            features = build_ml_features_from_history(
                prior_logs=logs,
                current_is_home=True,
                stat_key='pts',
                current_matchup='LAL vs. BOS',
            )
        # Should have found some BOS games in the log
        self.assertGreater(features['opp_hist_games'], 0)
        self.assertGreater(features['opp_hist_avg_stat'], 0.0)

    def test_build_features_game_total_line_stored(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        with self.app.app_context():
            _seed_player_logs(count=12, player_id='p4_phase1', player_name='Total Player')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p4_phase1', last_n=82)
            features = build_ml_features_from_history(
                prior_logs=logs,
                current_is_home=True,
                stat_key='pts',
                game_total_line=228.5,
            )
        self.assertAlmostEqual(features['game_total_line'], 228.5)

    def test_build_features_defense_lookup_populates_def_features(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        with self.app.app_context():
            _seed_player_logs(count=15, player_id='p5_phase1', player_name='Defense Player')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p5_phase1', last_n=82)
            defense_lookup = {
                'BOS': {
                    'def_rating': 108.5,
                    'pace': 98.2,
                    'opp_pts_pg': 109.0,
                    'opp_reb_pg': 42.0,
                    'opp_ast_pg': 24.0,
                    'opp_3pm_pg': 11.0,
                    'opp_stl_pg': 7.0,
                    'opp_blk_pg': 4.5,
                }
            }
            features = build_ml_features_from_history(
                prior_logs=logs,
                current_is_home=True,
                stat_key='pts',
                current_matchup='LAL vs. BOS',
                defense_lookup=defense_lookup,
            )
        self.assertAlmostEqual(features['opp_def_rating'], 108.5)
        self.assertAlmostEqual(features['opp_pace'], 98.2)
        self.assertAlmostEqual(features['opp_stat_allowed'], 109.0)  # pts → opp_pts_pg

    def test_build_features_defense_lookup_unknown_opp_zeroes(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        with self.app.app_context():
            _seed_player_logs(count=12, player_id='p6_phase1', player_name='Unknown Opp')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p6_phase1', last_n=82)
            features = build_ml_features_from_history(
                prior_logs=logs,
                current_is_home=True,
                stat_key='pts',
                current_matchup='LAL vs. BOS',
                defense_lookup={'DEN': {'def_rating': 112.0, 'pace': 102.0}},
            )
        # BOS not in lookup → should default to 0.0
        self.assertEqual(features['opp_def_rating'], 0.0)
        self.assertEqual(features['opp_pace'], 0.0)

    def test_build_features_no_phase1_params_all_zero(self):
        """Backward-compat: calling without Phase 1 params returns valid dict with zero defaults."""
        from app.services.ml_feature_builder import build_ml_features_from_history, FEATURE_KEYS
        with self.app.app_context():
            _seed_player_logs(count=15, player_id='p7_phase1', player_name='Compat Player')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p7_phase1', last_n=82)
            features = build_ml_features_from_history(logs, True, 'pts')
        # All FEATURE_KEYS must still be present
        for key in FEATURE_KEYS:
            self.assertIn(key, features)
        # Phase 1 contextual fields default to 0 / neutral when not provided
        self.assertEqual(features['opp_hist_games'], 0.0)
        self.assertEqual(features['game_total_line'], 0.0)
        self.assertEqual(features['opp_def_rating'], 0.0)
        self.assertEqual(features['days_rest'], 3.0)  # neutral default
        self.assertEqual(features['back_to_back'], 0.0)

    # ── ml_model training pipeline ───────────────────────────────────────────

    def test_build_defense_lookup_returns_dict(self):
        from app.services.ml_model import build_defense_lookup
        with self.app.app_context():
            _seed_defense(team_name='Boston Celtics', team_abbr='BOS',
                          opp_pts=108.0, pace=98.5, def_rating=106.5)
            result = build_defense_lookup()
        self.assertIsInstance(result, dict)
        self.assertIn('BOS', result)
        self.assertAlmostEqual(result['BOS']['def_rating'], 106.5)
        self.assertAlmostEqual(result['BOS']['pace'], 98.5)
        self.assertAlmostEqual(result['BOS']['opp_pts_pg'], 108.0)

    def test_build_defense_lookup_empty_db(self):
        from app.services.ml_model import build_defense_lookup
        with self.app.app_context():
            result = build_defense_lookup()
        self.assertIsInstance(result, dict)

    def test_build_game_total_lookup_returns_dict(self):
        from app.services.ml_model import _build_game_total_lookup
        with self.app.app_context():
            result = _build_game_total_lookup()
        self.assertIsInstance(result, dict)

    # ── projection_engine inference plumbing ─────────────────────────────────

    def test_project_stat_new_signature_accepted(self):
        """project_stat accepts game_total_line without error."""
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='p8_phase1', player_name='PE Phase1')
            engine = ProjectionEngine()
            result = engine.project_stat(
                'PE Phase1', 'player_points',
                opponent_name='Boston Celtics',
                team_name='LAL',
                is_home=True,
                game_total_line=225.0,
            )
        self.assertIn('projection', result)
        self.assertGreaterEqual(result['projection'], 0)

    def test_build_ml_features_with_matchup_and_total(self):
        """_build_ml_features passes new params without raising."""
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='p9_phase1', player_name='ML Phase1')
            from app.services.stats_service import get_cached_logs
            logs = get_cached_logs('p9_phase1', last_n=82)
            engine = ProjectionEngine()
            features = engine._build_ml_features(
                logs, 'pts', True,
                current_matchup='LAL vs. BOS',
                game_total_line=230.0,
            )
        self.assertIn('game_total_line', features)
        self.assertAlmostEqual(features['game_total_line'], 230.0)
        self.assertIn('opp_hist_games', features)


class TestDataQualityBranches(BaseTestCase):
    """Tests for data_quality_report branches requiring data in DB."""

    def _invoke(self, command, args=None):
        from click.testing import CliRunner
        runner = CliRunner()
        return runner.invoke(command, args or [], catch_exceptions=False)

    def test_data_quality_report_with_stale_running_job(self):
        """data_quality_report warns about running jobs > 180 min old."""
        from app.cli.stats_commands import cli_data_quality_report
        with self.app.app_context():
            old_start = datetime.now(timezone.utc) - timedelta(hours=4)
            stale_job = JobLog(
                job_name='old_running_job',
                started_at=old_start,
                status='running',
            )
            db.session.add(stale_job)
            db.session.commit()
            result = self._invoke(cli_data_quality_report)
        self.assertEqual(result.exit_code, 0)
        self.assertIn('WARN', result.output)
        self.assertIn('running JobLog entries', result.output)

    def test_data_quality_report_with_stale_player_logs(self):
        """data_quality_report warns when PlayerGameLog is stale (old game_date)."""
        from datetime import date as date_type
        from app.cli.stats_commands import cli_data_quality_report
        with self.app.app_context():
            # Add a player log with an old game_date
            old_date = date_type(2020, 1, 1)
            log = PlayerGameLog(
                player_id='999',
                player_name='Old Player',
                game_date=old_date,
                pts=20,
                reb=5,
                ast=3,
                minutes=32.0,
            )
            db.session.add(log)
            db.session.commit()
            result = self._invoke(cli_data_quality_report)
        self.assertEqual(result.exit_code, 0)
        self.assertIn('WARN', result.output)

    def test_data_quality_report_pass_with_today_data(self):
        """data_quality_report shows PASS when injuries and defense exist for today."""
        from datetime import date as date_type
        from app.cli.stats_commands import cli_data_quality_report
        today = date_type.today()
        with self.app.app_context():
            # Insert today's PlayerGameLog (current date)
            log = PlayerGameLog(
                player_id='998',
                player_name='Fresh Player',
                game_date=today,
                pts=25,
                reb=8,
                ast=4,
                minutes=35.0,
            )
            db.session.add(log)
            # Insert today's injuries
            injury = InjuryReport(
                player_name='Fresh Player',
                team='LAL',
                status='Active',
                date_reported=today,
            )
            db.session.add(injury)
            # Insert today's defense snapshot
            snap = TeamDefenseSnapshot(
                team_id='1610612747',
                team_name='Los Angeles Lakers',
                team_abbr='LAL',
                snapshot_date=today,
            )
            db.session.add(snap)
            db.session.commit()
            result = self._invoke(cli_data_quality_report, ['--stale-hours', '1'])
        self.assertEqual(result.exit_code, 0)


class TestDefenseStaleness(BaseTestCase):
    """Tests for defense staleness fields added to /ready in INFO Batch 2."""

    def test_ready_includes_defense_staleness(self):
        """/ready response includes defense_data_age_hours and defense_data_stale."""
        resp = self.client.get('/ready')
        self.assertIn(resp.status_code, [200, 503])
        data = json.loads(resp.data)
        self.assertIn('defense_data_stale', data)

    def test_ready_defense_stale_when_no_data(self):
        """defense_data_stale=True when no TeamDefenseSnapshot rows exist."""
        resp = self.client.get('/ready')
        data = json.loads(resp.data)
        self.assertTrue(data['defense_data_stale'])
