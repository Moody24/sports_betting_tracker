"""Comprehensive tests for uncovered service modules.

Covers: feature_engine, projection_engine, context_service,
        stats_service, matchup_service, value_detector, cli.
All external API calls are mocked.
"""

import json
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests as _requests

from app import db
from app.models import (
    InjuryReport,
    JobLog,
    ModelMetadata,
    PickContext,
    PlayerGameLog,
    TeamDefenseSnapshot,
    Bet,
)
from app.enums import Outcome
from tests.helpers import BaseTestCase, make_bet, make_user


# ---------------------------------------------------------------------------
# Helpers: seed realistic game log data into the test DB
# ---------------------------------------------------------------------------

def _seed_player_logs(count=20, player_id='101', player_name='LeBron James',
                      base_pts=25.0, base_reb=7.0, base_ast=7.0, base_fg3m=2.0,
                      base_minutes=35.0):
    """Insert ``count`` game logs for one player.  Returns the list of logs."""
    logs = []
    for i in range(count):
        log = PlayerGameLog(
            player_id=player_id,
            player_name=player_name,
            team_abbr='LAL',
            game_date=date(2026, 1, 1) + timedelta(days=i),
            matchup='LAL vs. BOS' if i % 2 == 0 else 'LAL @ MIA',
            minutes=base_minutes + (i % 5) - 2,
            pts=base_pts + (i % 7) - 3,
            reb=base_reb + (i % 4) - 1,
            ast=base_ast + (i % 3) - 1,
            fg3m=base_fg3m + (i % 3) - 1,
            stl=1.0 + (i % 2),
            blk=0.5 + (i % 2) * 0.5,
            tov=2.0,
            fgm=9.0,
            fga=18.0,
            ftm=5.0,
            fta=6.0,
            fg3a=5.0,
            plus_minus=3.0,
            home_away='home' if i % 2 == 0 else 'away',
            win_loss='W' if i % 3 != 0 else 'L',
        )
        db.session.add(log)
        logs.append(log)
    db.session.commit()
    return logs


def _seed_defense(team_name='Boston Celtics', team_abbr='BOS',
                  opp_pts=108.0, pace=98.5, def_rating=106.5):
    snap = TeamDefenseSnapshot(
        team_id='2',
        team_name=team_name,
        team_abbr=team_abbr,
        snapshot_date=date(2026, 2, 25),
        opp_pts_pg=opp_pts,
        opp_reb_pg=42.0,
        opp_ast_pg=24.0,
        opp_3pm_pg=11.0,
        opp_stl_pg=7.0,
        opp_blk_pg=4.5,
        opp_tov_pg=13.5,
        pace=pace,
        def_rating=def_rating,
    )
    db.session.add(snap)
    db.session.commit()
    return snap




class _FakeDataFrame:
    """Minimal DataFrame-like object for nba_api endpoint mocks in tests."""

    def __init__(self, rows):
        self._rows = list(rows)

    @property
    def empty(self):
        return len(self._rows) == 0

    def head(self, n):
        return _FakeDataFrame(self._rows[:n])

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, row


def _seed_injury(player_name='LeBron James', status='questionable'):
    report = InjuryReport(
        player_name=player_name,
        team='Los Angeles Lakers',
        status=status,
        detail='Knee soreness',
        date_reported=date.today(),
    )
    db.session.add(report)
    db.session.commit()
    return report


# ═══════════════════════════════════════════════════════════════════════════
# stats_service tests
# ═══════════════════════════════════════════════════════════════════════════

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
    @patch('app.services.stats_service.requests.get')
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
    @patch('app.services.stats_service.requests.get')
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


# ═══════════════════════════════════════════════════════════════════════════
# context_service tests
# ═══════════════════════════════════════════════════════════════════════════

class TestContextService(BaseTestCase):
    """Tests for context_service: injuries, B2B, rest days, game context."""

    def setUp(self):
        super().setUp()
        from app.services.context_service import clear_schedule_caches
        clear_schedule_caches()

    # -- fetch_espn_injuries --

    @patch('app.services.context_service.requests.get')
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

    @patch('app.services.context_service.requests.get')
    def test_fetch_espn_injuries_network_error(self, mock_get):
        from app.services.context_service import fetch_espn_injuries
        mock_get.side_effect = _requests.RequestException("timeout")
        self.assertEqual(fetch_espn_injuries(), [])

    @patch('app.services.context_service.requests.get')
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

    @patch('app.services.context_service.requests.get')
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

    @patch('app.services.context_service.requests.get')
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

    @patch('app.services.context_service.requests.get')
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

    @patch('app.services.context_service.requests.get')
    def test_check_b2b_false(self, mock_get):
        from app.services.context_service import check_back_to_back
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'events': []}
        mock_get.return_value = mock_resp
        self.assertFalse(check_back_to_back('Lakers'))

    @patch('app.services.context_service.requests.get')
    def test_check_b2b_network_error(self, mock_get):
        from app.services.context_service import check_back_to_back
        mock_get.side_effect = _requests.RequestException("timeout")
        self.assertFalse(check_back_to_back('Lakers'))

    # -- get_days_rest --

    @patch('app.services.context_service.requests.get')
    def test_get_days_rest_found(self, mock_get):
        from app.services.context_service import get_days_rest, _today_et

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

    @patch('app.services.context_service.requests.get')
    def test_get_days_rest_default(self, mock_get):
        from app.services.context_service import get_days_rest
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {'events': []}
        mock_get.return_value = mock_resp
        self.assertEqual(get_days_rest('Lakers', check_days=2), 2)

    @patch('app.services.context_service.requests.get')
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


# ═══════════════════════════════════════════════════════════════════════════
# matchup_service tests
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# feature_engine tests
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
# projection_engine tests
# ═══════════════════════════════════════════════════════════════════════════

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
                def side_effect(player_name, prop_type, opponent_name='', team_name='', is_home=True):
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
            self.assertEqual(result['projection'], 42.0)
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


# ═══════════════════════════════════════════════════════════════════════════
# value_detector tests
# ═══════════════════════════════════════════════════════════════════════════

class TestValueDetector(BaseTestCase):
    """Tests for ValueDetector: score_prop, score_all_todays_props, get_top_plays."""

    def test_score_prop_insufficient_games(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            _seed_player_logs(count=3, player_id='301')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='301'):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 25.5, -110, -110,
                )
            self.assertEqual(result['confidence_tier'], 'no_edge')
            self.assertEqual(result['projection'], 0)

    def test_score_prop_full_with_edge(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(30):
                log = PlayerGameLog(
                    player_id='302', player_name='High Scorer',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=35 + (i % 3), reb=7, ast=5, fg3m=3, minutes=36,
                    stl=1, blk=1, tov=2, fgm=12, fga=22,
                    ftm=7, fta=8, fg3a=8,
                )
                db.session.add(log)
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='302'):
                result = detector.score_prop(
                    'High Scorer', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                    is_home=True,
                )
            self.assertGreater(result['projection'], 0)
            self.assertGreater(result['model_prob_over'], 0.5)
            self.assertEqual(result['recommended_side'], 'over')

    def test_score_prop_zero_std(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                log = PlayerGameLog(
                    player_id='303', player_name='Consistent',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25, reb=7, ast=5, fg3m=2, minutes=35,
                    stl=1, blk=1, tov=2, fgm=10, fga=20,
                    ftm=5, fta=6, fg3a=5,
                )
                db.session.add(log)
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='303'):
                result = detector.score_prop(
                    'Consistent', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
            self.assertIn(result['model_prob_over'], (0.35, 0.65))

    def test_score_prop_under_recommended(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                log = PlayerGameLog(
                    player_id='304', player_name='Low Scorer',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=12 + (i % 3), reb=4, ast=2, fg3m=1, minutes=25,
                    stl=1, blk=0, tov=2, fgm=5, fga=12,
                    ftm=2, fta=3, fg3a=3,
                )
                db.session.add(log)
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='304'):
                result = detector.score_prop(
                    'Low Scorer', 'player_points',
                    line=30.5, over_odds=-110, under_odds=-110,
                )
            self.assertEqual(result['recommended_side'], 'under')

    def test_score_prop_with_game_id(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='305')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='305'):
                result = detector.score_prop(
                    'LeBron James', 'player_points',
                    line=25.5, over_odds=-110, under_odds=-110,
                    game_id='espn123',
                )
            self.assertEqual(result['game_id'], 'espn123')

    # -- implied_prob --

    def test_implied_prob_edge_cases(self):
        from app.services.value_detector import implied_prob
        self.assertAlmostEqual(implied_prob(100), 0.5)
        self.assertAlmostEqual(implied_prob(-100), 0.5)

    def test_devig_probs_balanced_market(self):
        from app.services.value_detector import devig_probs
        over, under = devig_probs(-110, -110)
        self.assertAlmostEqual(over, 0.5, places=3)
        self.assertAlmostEqual(under, 0.5, places=3)

    # -- decimal_odds --

    def test_decimal_odds_zero(self):
        from app.services.value_detector import decimal_odds
        self.assertEqual(decimal_odds(0), 2.0)

    # -- quarter_kelly --

    def test_quarter_kelly_zero_bankroll(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0.1, -110, 0), 0.0)

    def test_quarter_kelly_zero_odds(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0.1, 0, 1000), 0.0)

    def test_quarter_kelly_positive_odds(self):
        from app.services.value_detector import quarter_kelly
        stake = quarter_kelly(0.10, 200, 1000)
        self.assertGreater(stake, 0)
        self.assertLessEqual(stake, 50.0)

    # -- score_all_todays_props --

    def test_score_all_todays_props(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='306')
            _seed_injury('Hurt Player', 'out')
            mock_games = [{
                'odds_event_id': 'evt1', 'espn_id': 'espn1',
                'start_time': '2026-02-25T19:00:00Z',
                'home': {'name': 'Lakers'},
                'away': {'name': 'Celtics'},
            }]
            mock_props = {
                'player_points': [
                    {'player': 'LeBron James', 'line': 25.5,
                     'over_odds': -110, 'under_odds': -110},
                    {'player': 'Hurt Player', 'line': 10.5,
                     'over_odds': -110, 'under_odds': -110},
                    {'player': '', 'line': 0, 'over_odds': 0, 'under_odds': 0},
                ],
            }
            detector = ValueDetector()
            with patch('app.services.nba_service.fetch_player_props_for_event',
                       return_value=mock_props):
                with patch('app.services.projection_engine.find_player_id', return_value='306'):
                    scores = detector.score_all_todays_props(games=mock_games)
            self.assertGreaterEqual(len(scores), 1)

    def test_score_all_todays_props_no_games(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            detector = ValueDetector()
            with patch('app.services.nba_service.get_todays_games', return_value=[]):
                scores = detector.score_all_todays_props()
            self.assertEqual(scores, [])

    def test_score_all_todays_props_fetch_exception(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            mock_games = [{'odds_event_id': 'evt1', 'espn_id': 'e1',
                          'start_time': '', 'home': {'name': 'A'},
                          'away': {'name': 'B'}}]
            detector = ValueDetector()
            with patch('app.services.nba_service.fetch_player_props_for_event',
                       side_effect=Exception("fail")):
                scores = detector.score_all_todays_props(games=mock_games)
            self.assertEqual(scores, [])

    def test_score_all_todays_props_no_event_id(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            mock_games = [{'odds_event_id': '', 'espn_id': 'e1',
                          'start_time': '', 'home': {'name': 'A'},
                          'away': {'name': 'B'}}]
            detector = ValueDetector()
            scores = detector.score_all_todays_props(games=mock_games)
            self.assertEqual(scores, [])

    def test_score_all_todays_props_resolves_player_side_from_team_abbr(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            db.session.add(PlayerGameLog(
                player_id='777',
                player_name='Away Player',
                team_abbr='BOS',
                game_date=date(2026, 2, 25),
                pts=20,
            ))
            db.session.commit()

            mock_games = [{
                'odds_event_id': 'evt1',
                'espn_id': 'espn1',
                'start_time': '2026-02-25T19:00:00Z',
                'home': {'name': 'Los Angeles Lakers', 'abbr': 'LAL'},
                'away': {'name': 'Boston Celtics', 'abbr': 'BOS'},
            }]
            mock_props = {
                'player_points': [{
                    'player': 'Away Player',
                    'line': 15.5,
                    'over_odds': -110,
                    'under_odds': -110,
                }],
            }
            detector = ValueDetector()
            with patch('app.services.nba_service.fetch_player_props_for_event', return_value=mock_props):
                with patch.object(detector, 'score_prop', return_value={
                    'edge': 0.1, 'confidence_tier': 'moderate', 'games_played': 20,
                }) as score_mock:
                    detector.score_all_todays_props(games=mock_games)

            self.assertTrue(score_mock.called)
            kwargs = score_mock.call_args.kwargs
            self.assertEqual(kwargs['team_name'], 'Boston Celtics')
            self.assertEqual(kwargs['opponent_name'], 'Los Angeles Lakers')
            self.assertFalse(kwargs['is_home'])

    # -- get_top_plays --

    def test_get_top_plays(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            detector = ValueDetector()
            mock_scores = [
                {'edge': 0.20, 'confidence_tier': 'strong', 'games_played': 30},
                {'edge': 0.01, 'confidence_tier': 'no_edge', 'games_played': 30},
                {'edge': 0.10, 'confidence_tier': 'moderate', 'games_played': 5},
            ]
            with patch.object(detector, 'score_all_todays_props', return_value=mock_scores):
                top = detector.get_top_plays(min_edge=0.03)
            self.assertEqual(len(top), 1)

    def test_recommend_best_parlay_returns_target_range(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            detector = ValueDetector()
            mock_scores = [
                {
                    'player': 'A', 'prop_type': 'player_points', 'line': 20.5,
                    'recommended_side': 'over', 'recommended_odds': -150,
                    'edge': 0.12, 'confidence_tier': 'strong', 'games_played': 20,
                    'game_id': 'g1',
                },
                {
                    'player': 'B', 'prop_type': 'player_points', 'line': 18.5,
                    'recommended_side': 'under', 'recommended_odds': -150,
                    'edge': 0.11, 'confidence_tier': 'strong', 'games_played': 20,
                    'game_id': 'g2',
                },
                {
                    'player': 'C', 'prop_type': 'player_points', 'line': 16.5,
                    'recommended_side': 'over', 'recommended_odds': 130,
                    'edge': 0.2, 'confidence_tier': 'strong', 'games_played': 20,
                    'game_id': 'g3',
                },
            ]
            parlay = detector.recommend_best_parlay(
                scores=mock_scores,
                min_edge=0.08,
                min_odds=100,
                max_odds=200,
                min_legs=2,
                max_legs=3,
            )
            self.assertIsNotNone(parlay)
            self.assertGreaterEqual(parlay['combined_odds'], 100)
            self.assertLessEqual(parlay['combined_odds'], 200)
            self.assertIn(len(parlay['legs']), (2, 3))

    def test_score_prop_strong_requires_projection_confidence(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        detector.engine = MagicMock()
        detector.engine.project_stat.return_value = {
            'projection': 40.0,
            'std_dev': 4.0,
            'games_played': 20,
            'confidence': 'low',
            'context_notes': [],
            'z_score': 0,
            'projection_source': 'heuristic',
            'breakdown': {},
        }
        result = detector.score_prop(
            player_name='Test Player',
            prop_type='player_points',
            line=20.5,
            over_odds=-110,
            under_odds=-110,
        )
        self.assertEqual(result['confidence_tier'], 'moderate')

    def test_score_prop_strong_when_confidence_medium_or_high(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        detector.engine = MagicMock()
        detector.engine.project_stat.return_value = {
            'projection': 40.0,
            'std_dev': 4.0,
            'games_played': 20,
            'confidence': 'high',
            'context_notes': [],
            'z_score': 0,
            'projection_source': 'heuristic',
            'breakdown': {},
        }
        result = detector.score_prop(
            player_name='Test Player',
            prop_type='player_points',
            line=20.5,
            over_odds=-110,
            under_odds=-110,
        )
        self.assertEqual(result['confidence_tier'], 'strong')

    # -- _model_prob_over --

    def test_model_prob_over_scipy(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        prob = detector._model_prob_over(30, 25, 5)
        self.assertGreater(prob, 0.5)

    # -- _empty_score --

    def test_empty_score(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        result = detector._empty_score('Player', 'player_points', 25.5, -110, -110, 'g1')
        self.assertEqual(result['projection'], 0)
        self.assertEqual(result['game_id'], 'g1')
        self.assertEqual(result['confidence_tier'], 'no_edge')


class TestMLModel(BaseTestCase):
    """Targeted coverage for app.services.ml_model."""

    def test_build_training_data_insufficient(self):
        from app.services import ml_model
        with self.app.app_context():
            feats, targets = ml_model._build_training_data('player_points')
        self.assertIsNone(feats)
        self.assertIsNone(targets)

    def test_build_training_data_has_new_feature_keys(self):
        from app.services import ml_model
        with self.app.app_context():
            _seed_player_logs(count=12, player_id='501', player_name='Feature Player')
            with patch.object(ml_model, 'MIN_TRAIN_SAMPLES', 1):
                feats, targets = ml_model._build_training_data('player_points')
        self.assertIsNotNone(feats)
        self.assertIsNotNone(targets)
        self.assertGreater(len(feats), 0)
        sample = feats[0]
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
            self.assertIn(key, sample)

    def test_build_training_rows_are_globally_date_sorted(self):
        from app.services import ml_model
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='200', player_name='Player A')
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id='100',
                    player_name='Player B',
                    team_abbr='BOS',
                    game_date=date(2026, 2, 1) + timedelta(days=i),
                    matchup='BOS vs. NYK',
                    minutes=30,
                    pts=20,
                    reb=5,
                    ast=5,
                    fg3m=2,
                    tov=2,
                    fgm=8,
                    fga=16,
                    ftm=4,
                    fta=5,
                    fg3a=6,
                    home_away='home',
                ))
            db.session.commit()
            with patch.object(ml_model, 'MIN_TRAIN_SAMPLES', 1):
                rows = ml_model._build_training_rows('player_points')

        self.assertTrue(rows)
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates))

    def test_training_share_features_zero_when_cache_incomplete(self):
        from app.services import ml_model
        with self.app.app_context():
            _seed_player_logs(count=12, player_id='511', player_name='Solo Player')
            with patch.object(ml_model, 'MIN_TRAIN_SAMPLES', 1):
                feats, _ = ml_model._build_training_data('player_points')
        self.assertTrue(feats)
        sample = feats[0]
        self.assertEqual(sample['fga_share_last_5'], 0.0)
        self.assertEqual(sample['pts_share_last_5'], 0.0)
        self.assertEqual(sample['usage_share_last_5'], 0.0)
        self.assertGreaterEqual(sample['lead_usage_rate_last_10'], 0.0)
        self.assertLessEqual(sample['lead_usage_rate_last_10'], 1.0)

    def test_inference_share_features_non_zero_with_full_team_cache(self):
        from app.services.projection_engine import ProjectionEngine
        from app.services.stats_service import get_cached_logs

        with self.app.app_context():
            base_date = date(2026, 3, 1)
            for pidx in range(6):
                player_id = f'6{pidx}'
                for didx in range(12):
                    fga = 22 if pidx == 0 else 10
                    pts = 30 if pidx == 0 else 12
                    db.session.add(PlayerGameLog(
                        player_id=player_id,
                        player_name=f'Player {pidx}',
                        team_abbr='TST',
                        game_date=base_date + timedelta(days=didx),
                        matchup='TST vs. OPP',
                        minutes=34,
                        pts=pts,
                        reb=5,
                        ast=4,
                        fg3m=2,
                        tov=2,
                        fgm=8,
                        fga=fga,
                        ftm=4,
                        fta=5,
                        fg3a=6,
                        home_away='home' if didx % 2 == 0 else 'away',
                    ))
            db.session.commit()

            logs = get_cached_logs('60', last_n=82)
            features = ProjectionEngine()._build_ml_features(logs, 'pts', is_home=True)

        for key in ('fga_share_last_5', 'pts_share_last_5', 'usage_share_last_5', 'lead_usage_rate_last_10'):
            self.assertIn(key, features)
        self.assertGreater(features['fga_share_last_5'], 0.0)
        self.assertLessEqual(features['fga_share_last_5'], 1.0)
        self.assertGreater(features['usage_share_last_5'], 0.0)
        self.assertLessEqual(features['usage_share_last_5'], 1.0)

    def test_feature_builder_order_invariant(self):
        from app.services.ml_feature_builder import build_ml_features_from_history
        from app.services.stats_service import get_cached_logs

        with self.app.app_context():
            _seed_player_logs(count=15, player_id='701', player_name='Order Player')
            logs = get_cached_logs('701', last_n=82)
            asc_logs = list(reversed(logs))
            f1 = build_ml_features_from_history(logs, True, 'pts', all_history_logs=logs)
            f2 = build_ml_features_from_history(asc_logs, True, 'pts', all_history_logs=asc_logs)

        self.assertEqual(set(f1.keys()), set(f2.keys()))
        self.assertAlmostEqual(f1['avg_stat_last_5'], f2['avg_stat_last_5'])
        self.assertAlmostEqual(f1['min_last_3_avg'], f2['min_last_3_avg'])

    def test_train_model_success_persists_metadata(self):
        from app.services import ml_model
        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='projection_player_points',
                model_type='xgboost_regressor',
                version='old',
                file_path='/tmp/old.json',
                training_date=datetime.now(timezone.utc),
                training_samples=100,
                val_mae=9.9,
                is_active=True,
            ))
            db.session.commit()

            mock_features = [
                {'avg_stat_last_5': 10.0, 'games_played': 12},
                {'avg_stat_last_5': 11.0, 'games_played': 13},
                {'avg_stat_last_5': 12.0, 'games_played': 14},
                {'avg_stat_last_5': 13.0, 'games_played': 15},
                {'avg_stat_last_5': 14.0, 'games_played': 16},
            ]
            mock_targets = [10, 12, 14, 16, 18]

            fake_model = MagicMock()
            fake_model.predict.return_value = [11.0]
            mock_rows = [
                (date(2026, 1, 11), 'p1', mock_features[0], mock_targets[0]),
                (date(2026, 1, 12), 'p1', mock_features[1], mock_targets[1]),
                (date(2026, 1, 13), 'p1', mock_features[2], mock_targets[2]),
                (date(2026, 1, 14), 'p1', mock_features[3], mock_targets[3]),
                (date(2026, 1, 15), 'p1', mock_features[4], mock_targets[4]),
            ]
            with patch.object(ml_model, '_build_training_rows', return_value=mock_rows):
                with patch.object(ml_model, '_ensure_model_dir'):
                    with patch('xgboost.XGBRegressor', return_value=fake_model):
                        with patch('sklearn.metrics.mean_absolute_error', return_value=1.234):
                            result = ml_model.train_model('player_points')

            self.assertEqual(result['stat_type'], 'player_points')
            active = ModelMetadata.query.filter_by(model_name='projection_player_points', is_active=True).all()
            self.assertEqual(len(active), 1)
            inactive = ModelMetadata.query.filter_by(model_name='projection_player_points', is_active=False).all()
            self.assertGreaterEqual(len(inactive), 1)

    def test_load_and_predict_paths(self):
        from app.services import ml_model
        with self.app.app_context():
            # No active model
            model, names = ml_model.load_active_model('player_points')
            self.assertIsNone(model)
            self.assertIsNone(names)
            self.assertEqual(ml_model.predict_stat('player_points', {'x': 1}), 0.0)

            # Active model with parseable metadata
            model_path = '/tmp/test_model.json'
            with open(model_path, 'w', encoding='utf-8') as f:
                f.write('{}')
            db.session.add(ModelMetadata(
                model_name='projection_player_points',
                model_type='xgboost_regressor',
                version='v1',
                file_path=model_path,
                training_date=datetime.now(timezone.utc),
                training_samples=10,
                val_mae=1.0,
                is_active=True,
                metadata_json='{"feature_names":["f1","f2"]}',
            ))
            db.session.commit()

            fake_loaded_model = MagicMock()
            fake_loaded_model.predict.return_value = [22.26]
            with patch('xgboost.XGBRegressor', return_value=fake_loaded_model):
                pred = ml_model.predict_stat('player_points', {'f1': 1.0, 'f2': 2.0})
            self.assertEqual(pred, 22.3)

            with patch('xgboost.XGBRegressor', return_value=fake_loaded_model):
                with patch.object(ml_model, 'load_active_model', return_value=(fake_loaded_model, ['f1'])):
                    fake_loaded_model.predict.side_effect = RuntimeError('boom')
                    self.assertEqual(ml_model.predict_stat('player_points', {'f1': 1.0}), 0.0)

    def test_retrain_all_models_and_performance(self):
        from app.services import ml_model
        with self.app.app_context():
            with patch.object(ml_model, 'train_model', side_effect=[
                {'error': 'Insufficient training data', 'stat_type': 'player_points'},
                {'stat_type': 'player_rebounds', 'mae': 2.0, 'train_samples': 10, 'val_samples': 2, 'model_path': '/tmp/a'},
                {'stat_type': 'player_assists', 'mae': 1.0, 'train_samples': 10, 'val_samples': 2, 'model_path': '/tmp/b'},
                {'stat_type': 'player_threes', 'mae': 0.8, 'train_samples': 10, 'val_samples': 2, 'model_path': '/tmp/c'},
                {'stat_type': 'player_steals', 'mae': 0.3, 'train_samples': 10, 'val_samples': 2, 'model_path': '/tmp/d'},
                {'stat_type': 'player_blocks', 'mae': 0.4, 'train_samples': 10, 'val_samples': 2, 'model_path': '/tmp/e'},
            ]):
                out = ml_model.retrain_all_models()
            self.assertIn('player_points', out)
            self.assertIn('player_threes', out)
            self.assertIn('player_steals', out)
            self.assertIn('player_blocks', out)

            db.session.add(ModelMetadata(
                model_name='projection_player_points',
                model_type='xgboost_regressor',
                version='perf',
                file_path='/tmp/perf.json',
                training_date=datetime.now(timezone.utc),
                training_samples=123,
                val_mae=4.2,
                val_accuracy=None,
                is_active=True,
            ))
            db.session.commit()
            perf = ml_model.get_model_performance()
            self.assertTrue(any(m['name'] == 'projection_player_points' for m in perf))


class TestModelStorage(BaseTestCase):
    """Coverage for model artifact storage helpers."""

    def test_persist_local_mode_returns_local_path(self):
        from app.services import model_storage

        local_path = '/tmp/local_model.json'
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write('{}')

        with patch.dict(os.environ, {'MODEL_STORAGE': 'local'}, clear=False):
            out = model_storage.persist_model_artifact(local_path, 'local_model.json')
        self.assertEqual(out, local_path)

    def test_persist_s3_mode_uploads_and_returns_s3_uri(self):
        from app.services import model_storage

        local_path = '/tmp/s3_model_upload.json'
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write('{}')

        fake_client = MagicMock()
        with patch.dict(
            os.environ,
            {
                'MODEL_STORAGE': 's3',
                'S3_MODEL_BUCKET': 'test-bucket',
                'S3_MODEL_PREFIX': 'models/',
            },
            clear=False,
        ):
            with patch.object(model_storage, '_get_s3_client', return_value=fake_client):
                out = model_storage.persist_model_artifact(local_path, 'projection_player_points_x.json')

        self.assertEqual(out, 's3://test-bucket/models/projection_player_points_x.json')
        fake_client.upload_file.assert_called_once_with(
            local_path, 'test-bucket', 'models/projection_player_points_x.json'
        )

    def test_materialize_s3_downloads_to_cache(self):
        from app.services import model_storage
        from uuid import uuid4

        uri = f's3://test-bucket/models/model_{uuid4().hex}.json'
        fake_client = MagicMock()

        def _fake_download(_bucket, _key, dest):
            with open(dest, 'w', encoding='utf-8') as f:
                f.write('{}')

        fake_client.download_file.side_effect = _fake_download

        with patch.object(model_storage, '_get_s3_client', return_value=fake_client):
            local_path = model_storage.materialize_model_artifact(uri)

        self.assertIsNotNone(local_path)
        self.assertTrue(os.path.exists(local_path))
        fake_client.download_file.assert_called_once()


class TestScheduler(BaseTestCase):
    """Targeted coverage for app.services.scheduler."""

    def setUp(self):
        super().setUp()
        from app.services import scheduler as scheduler_module
        scheduler_module._scheduler_lock_fd = None

    def test_acquire_scheduler_lock_paths(self):
        from app.services import scheduler as scheduler_module
        self.assertTrue(scheduler_module._acquire_scheduler_lock('/tmp/test_scheduler.lock'))
        self.assertTrue(scheduler_module._acquire_scheduler_lock('/tmp/test_scheduler.lock'))
        scheduler_module._scheduler_lock_fd = None
        with patch('app.services.scheduler.fcntl.flock', side_effect=BlockingIOError):
            self.assertFalse(scheduler_module._acquire_scheduler_lock('/tmp/test_scheduler2.lock'))

    def test_log_job_success_and_failure(self):
        from app.services import scheduler as scheduler_module
        with patch('app.create_app', return_value=self.app):
            scheduler_module._log_job('ok_job', lambda: None)
            scheduler_module._log_job('bad_job', lambda: (_ for _ in ()).throw(RuntimeError('x')))
        with self.app.app_context():
            ok = JobLog.query.filter_by(job_name='ok_job').first()
            bad = JobLog.query.filter_by(job_name='bad_job').first()
            self.assertEqual(ok.status, 'success')
            self.assertEqual(bad.status, 'failed')
            self.assertIn('x', bad.message)

    def test_close_stale_running_jobs_marks_old_rows_failed(self):
        from app.services import scheduler as scheduler_module

        with self.app.app_context():
            stale_row = JobLog(
                job_name='stale_job',
                started_at=datetime.now(timezone.utc) - timedelta(hours=5),
                status='running',
            )
            fresh_row = JobLog(
                job_name='fresh_job',
                started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                status='running',
            )
            db.session.add_all([stale_row, fresh_row])
            db.session.commit()

            scheduler_module._close_stale_running_jobs(db, JobLog)

            stale_row = db.session.get(JobLog, stale_row.id)
            fresh_row = db.session.get(JobLog, fresh_row.id)
            self.assertEqual(stale_row.status, 'failed')
            self.assertIsNotNone(stale_row.finished_at)
            self.assertIn('Marked stale after', stale_row.message)
            self.assertEqual(fresh_row.status, 'running')
            self.assertIsNone(fresh_row.finished_at)

    def test_log_job_closes_stale_before_new_run(self):
        from app.services import scheduler as scheduler_module

        with self.app.app_context():
            db.session.add(JobLog(
                job_name='yesterday_stats_refresh',
                started_at=datetime.now(timezone.utc) - timedelta(days=1),
                status='running',
            ))
            db.session.commit()

        with patch('app.create_app', return_value=self.app):
            scheduler_module._log_job('stats_refresh', lambda: None)

        with self.app.app_context():
            stale = JobLog.query.filter_by(job_name='yesterday_stats_refresh').first()
            latest = JobLog.query.filter_by(job_name='stats_refresh').order_by(JobLog.id.desc()).first()
            self.assertEqual(stale.status, 'failed')
            self.assertIsNotNone(stale.finished_at)
            self.assertEqual(latest.status, 'success')

    def test_refresh_jobs_and_projection_job(self):
        from app.services import scheduler as scheduler_module
        with patch('app.create_app', return_value=self.app):
            with patch('app.services.stats_service.refresh_completed_game_logs', return_value={
                'final_games_seen': 2,
                'players_upserted': 20,
                'rows_inserted': 40,
                'rows_updated': 15,
            }):
                scheduler_module.refresh_player_stats()
            with patch('app.services.matchup_service.refresh_all_team_defense', return_value=30):
                scheduler_module.refresh_defense_data()
            with patch('app.services.context_service.refresh_injuries', return_value=12):
                scheduler_module.refresh_injury_reports()
            fake_detector = MagicMock()
            fake_detector.score_all_todays_props.return_value = [{'edge': 0.16}, {'edge': 0.04}]
            with patch('app.services.value_detector.ValueDetector', return_value=fake_detector):
                with patch('app.services.projection_engine.ProjectionEngine', return_value=MagicMock()):
                    scheduler_module.run_projections()

    def test_resolve_and_grade(self):
        from app.services import scheduler as scheduler_module
        with self.app.app_context():
            user = make_user('scheduser', 'sched@example.com')
            db.session.add(user)
            db.session.commit()
            bet = make_bet(
                user.id,
                external_game_id='game123',
                outcome=Outcome.PENDING.value,
                bet_type='over',
                over_under_line=210.5,
            )
            db.session.add(bet)
            db.session.commit()
            bet_id = bet.id

        with patch('app.create_app', return_value=self.app):
            with patch(
                'app.services.nba_service.resolve_pending_bets',
                side_effect=lambda pending: [(pending[0], Outcome.WIN.value, 225.0)],
            ):
                scheduler_module.resolve_and_grade()
            with self.app.app_context():
                updated = db.session.get(Bet, bet_id)
                self.assertEqual(updated.outcome, Outcome.WIN.value)
                self.assertEqual(updated.actual_total, 225.0)

    def test_retrain_models_guardrails_and_train_path(self):
        from app.services import scheduler as scheduler_module
        now = datetime.now(timezone.utc)
        with self.app.app_context():
            db.session.add(PlayerGameLog(
                player_id='p1', player_name='P1', game_date=date(2026, 1, 1), pts=10, minutes=30
            ))
            db.session.commit()

        with patch('app.create_app', return_value=self.app):
            # Skip path: recent model + no new rows
            with self.app.app_context():
                db.session.add(ModelMetadata(
                    model_name='projection_player_points',
                    model_type='xgboost_regressor',
                    version='recent',
                    file_path='/tmp/recent.json',
                    training_date=now,
                    training_samples=10,
                    val_mae=1.0,
                    is_active=True,
                    metadata_json='{"player_game_log_rows": 1}',
                ))
                db.session.commit()
            with patch('app.services.ml_model.retrain_all_models') as retrain_mock:
                with patch('app.services.pick_quality_model.train_pick_quality_model', return_value={'ok': 1}) as pq_mock:
                    scheduler_module.retrain_models()
            retrain_mock.assert_not_called()
            pq_mock.assert_called_once()

            # Train path: old model + stale row count
            with self.app.app_context():
                ModelMetadata.query.delete()
                db.session.add(ModelMetadata(
                    model_name='projection_player_points',
                    model_type='xgboost_regressor',
                    version='old',
                    file_path='/tmp/old_sched.json',
                    training_date=now - timedelta(days=10),
                    training_samples=10,
                    val_mae=1.0,
                    is_active=True,
                    metadata_json='{"player_game_log_rows": 0}',
                ))
                db.session.commit()
            with patch('app.services.ml_model.retrain_all_models', return_value={'ok': 1}) as retrain_mock:
                with patch('app.services.pick_quality_model.train_pick_quality_model', return_value={'ok': 1}):
                    scheduler_module.retrain_models()
            retrain_mock.assert_called_once()

    def test_generate_daily_auto_picks_creates_separated_bets(self):
        from app.services import scheduler as scheduler_module
        with patch('app.create_app', return_value=self.app):
            fake_detector = MagicMock()
            fake_detector.score_all_todays_props.return_value = [
                {
                    'player': 'LeBron James',
                    'prop_type': 'player_points',
                    'line': 27.5,
                    'recommended_side': 'over',
                    'recommended_odds': -110,
                    'edge': 0.16,
                    'edge_over': 0.16,
                    'edge_under': -0.16,
                    'confidence_tier': 'strong',
                    'projection': 30.0,
                    'games_played': 20,
                    'game_id': 'espn1',
                    'home_team': 'Boston Celtics',
                    'away_team': 'Los Angeles Lakers',
                    'match_date': '2026-03-01',
                },
                {
                    'player': 'Jayson Tatum',
                    'prop_type': 'player_points',
                    'line': 28.5,
                    'recommended_side': 'under',
                    'recommended_odds': 130,
                    'edge': 0.18,
                    'edge_over': -0.18,
                    'edge_under': 0.18,
                    'confidence_tier': 'strong',
                    'projection': 23.0,
                    'games_played': 20,
                    'game_id': 'espn2',
                    'home_team': 'Boston Celtics',
                    'away_team': 'Los Angeles Lakers',
                    'match_date': '2026-03-01',
                },
                {
                    'player': 'Jaylen Brown',
                    'prop_type': 'player_points',
                    'line': 23.5,
                    'recommended_side': 'over',
                    'recommended_odds': 125,
                    'edge': 0.17,
                    'edge_over': 0.17,
                    'edge_under': -0.17,
                    'confidence_tier': 'strong',
                    'projection': 27.0,
                    'games_played': 20,
                    'game_id': 'espn3',
                    'home_team': 'Boston Celtics',
                    'away_team': 'Los Angeles Lakers',
                    'match_date': '2026-03-01',
                },
            ]
            with patch('app.services.value_detector.ValueDetector', return_value=fake_detector):
                with patch('app.services.projection_engine.ProjectionEngine', return_value=MagicMock()):
                    with patch('app.services.stats_service.find_player_id', return_value='123'):
                        scheduler_module.generate_daily_auto_picks()

        with self.app.app_context():
            auto_bets = Bet.query.filter_by(source='auto_generated').all()
            self.assertGreaterEqual(len(auto_bets), 2)
            self.assertTrue(all(b.user.username == '__autopicks__' for b in auto_bets))
            self.assertGreaterEqual(PickContext.query.count(), 1)

    def test_init_scheduler_adds_jobs(self):
        from app.services import scheduler as scheduler_module

        class FakeScheduler:
            def __init__(self):
                self.running = False
                self.jobs = []
                self.started = False

            def add_job(self, func, trigger, id=None, replace_existing=None):
                self.jobs.append((id, trigger))

            def start(self):
                self.started = True

            def get_jobs(self):
                return self.jobs

        fake = FakeScheduler()
        with patch.object(scheduler_module, 'scheduler', fake):
            with patch.object(scheduler_module, 'CronTrigger', side_effect=lambda **kw: kw):
                with patch.object(scheduler_module, '_acquire_scheduler_lock', return_value=True):
                    scheduler_module.init_scheduler(self.app)
        self.assertTrue(fake.started)
        self.assertEqual(len(fake.jobs), 16)


# ═══════════════════════════════════════════════════════════════════════════
# pick_quality_model tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPickQualityModel(BaseTestCase):
    """Tests for pick_quality_model helpers and data shaping."""

    def test_build_training_data_insufficient(self):
        from app.services import pick_quality_model

        with self.app.app_context():
            user = make_user('pq1', 'pq1@example.com')
            db.session.add(user)
            db.session.commit()

            bet = make_bet(user.id, outcome='win')
            db.session.add(bet)
            db.session.commit()

            db.session.add(PickContext(
                bet_id=bet.id,
                context_json='{"projected_edge": 1.2}',
            ))
            db.session.commit()

            features, targets, dates = pick_quality_model._build_training_data()
            self.assertIsNone(features)
            self.assertIsNone(targets)
            self.assertIsNone(dates)

    def test_build_training_data_encodes_and_normalizes(self):
        from app.services import pick_quality_model

        with self.app.app_context():
            user = make_user('pq2', 'pq2@example.com')
            db.session.add(user)
            db.session.commit()

            bet1 = make_bet(user.id, outcome='win')
            bet2 = make_bet(user.id, outcome='lose')
            db.session.add_all([bet1, bet2])
            db.session.commit()

            db.session.add(PickContext(
                bet_id=bet1.id,
                context_json=(
                    '{"projected_edge": "2.5", "back_to_back": true, '
                    '"player_last5_trend": "hot", "minutes_trend": "increasing", '
                    '"confidence_tier": "strong", "injury_returning": false, '
                    '"opp_defense_rating": 110.5, "opp_pace": 100.2, "opp_matchup_adj": 1.05}'
                ),
            ))
            db.session.add(PickContext(
                bet_id=bet2.id,
                context_json=(
                    '{"projected_edge": "bad", "back_to_back": false, '
                    '"player_last5_trend": "cold", "minutes_trend": "decreasing", '
                    '"confidence_tier": "slight", "injury_returning": true, '
                    '"opp_defense_rating": 108.0, "opp_pace": 98.5, "opp_matchup_adj": 0.95}'
                ),
            ))
            db.session.commit()

            with patch.object(pick_quality_model, 'MIN_RESOLVED_PICKS', 2):
                features, targets, dates = pick_quality_model._build_training_data()

            self.assertEqual(len(features), 2)
            self.assertCountEqual(targets, [1, 0])
            self.assertEqual(len(dates), 2)
            # Find the win and lose rows by target value (order may vary)
            win_idx = targets.index(1)
            lose_idx = targets.index(0)
            self.assertEqual(features[win_idx]['player_trend'], 1)
            self.assertEqual(features[win_idx]['minutes_trend'], 1)
            self.assertEqual(features[win_idx]['confidence_tier_num'], 3)
            self.assertEqual(features[win_idx]['injury_returning'], 0)
            self.assertEqual(features[lose_idx]['projected_edge'], 0.0)
            self.assertEqual(features[lose_idx]['player_trend'], -1)

    def test_get_feature_importance_returns_active_model_features(self):
        from app.services.pick_quality_model import get_feature_importance

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=10,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{"top_features": [["projected_edge", 0.8]]}',
            ))
            db.session.commit()

            feats = get_feature_importance()
            self.assertEqual(feats, [['projected_edge', 0.8]])

    def test_no_model_result_shape(self):
        from app.services.pick_quality_model import _no_model_result

        result = _no_model_result()
        self.assertEqual(result['win_probability'], 0.5)
        self.assertEqual(result['recommendation'], 'no_model')
        self.assertEqual(result['red_flags'], [])
        self.assertIsNone(result['model_version'])

    def test_model_name_global_and_user(self):
        from app.services.pick_quality_model import _model_name

        self.assertEqual(_model_name(None), 'pick_quality_nba')
        self.assertEqual(_model_name(42), 'pick_quality_nba_user_42')

    def test_get_feature_importance_invalid_metadata_json(self):
        from app.services.pick_quality_model import get_feature_importance

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='vbad',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=10,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{bad json',
            ))
            db.session.commit()

            feats = get_feature_importance()
            self.assertEqual(feats, [])

    def test_train_pick_quality_model_success(self):
        from app.services import pick_quality_model

        class _SliceableProba:
            def __getitem__(self, item):
                if isinstance(item, tuple):
                    return [0.8, 0.2]
                return [[0.2, 0.8], [0.8, 0.2]]

        class _FakeXGBClassifier:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.feature_importances_ = [0.9, 0.1]

            def fit(self, *args, **kwargs):
                return None

            def predict(self, _x):
                return [1, 0]

            def predict_proba(self, _x):
                return _SliceableProba()

            def save_model(self, _path):
                return None

        fake_xgboost = SimpleNamespace(XGBClassifier=_FakeXGBClassifier)
        fake_np = SimpleNamespace(array=lambda x: x)
        fake_metrics = SimpleNamespace(
            accuracy_score=lambda y_true, y_pred: 0.5,
            log_loss=lambda y_true, y_prob: 0.7,
        )
        fake_model_selection = SimpleNamespace(
            train_test_split=lambda X, y, test_size, stratify, random_state: (
                X[:2], X[2:], y[:2], y[2:],
            )
        )

        features = [
            {'projected_edge': 1.0, 'player_trend': 1},
            {'projected_edge': 0.5, 'player_trend': 0},
            {'projected_edge': -0.2, 'player_trend': -1},
            {'projected_edge': 0.1, 'player_trend': 0},
        ]
        targets = [1, 0, 1, 0]

        with self.app.app_context():
            # Cover "deactivate previous active model" branch.
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='old',
                file_path='/tmp/old.json',
                training_date=datetime.now(timezone.utc),
                training_samples=10,
                val_accuracy=0.55,
                is_active=True,
                metadata_json='{}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {
                'xgboost': fake_xgboost,
                'numpy': fake_np,
                'sklearn.metrics': fake_metrics,
                'sklearn.model_selection': fake_model_selection,
            }):
                with patch.object(pick_quality_model, '_build_training_data', return_value=(features, targets, [None] * len(targets))):
                    with patch('app.services.pick_quality_model.persist_model_artifact', return_value='s3://bucket/model.json'):
                        result = pick_quality_model.train_pick_quality_model()

            self.assertIn('accuracy', result)
            self.assertEqual(result['model_path'], 's3://bucket/model.json')
            active = ModelMetadata.query.filter_by(model_name='pick_quality_nba', is_active=True).all()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].file_path, 's3://bucket/model.json')

    def test_predict_pick_quality_success(self):
        from app.services import pick_quality_model

        class _FakeXGBClassifier:
            def load_model(self, _path):
                return None

            def predict_proba(self, _x):
                return [[0.35, 0.65]]

        fake_xgboost = SimpleNamespace(XGBClassifier=_FakeXGBClassifier)
        fake_np = SimpleNamespace(array=lambda x: x)

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='pq_v2',
                file_path='s3://bucket/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=25,
                val_accuracy=0.62,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge","player_trend","minutes_trend","confidence_tier_num","injury_returning"]}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': fake_xgboost, 'numpy': fake_np}):
                with patch('app.services.pick_quality_model.materialize_model_artifact', return_value='/tmp/model.json'):
                    result = pick_quality_model.predict_pick_quality({
                        'projected_edge': 1.6,
                        'back_to_back': True,
                        'player_variance': 9.5,
                        'injury_returning': True,
                        'player_last5_trend': 'cold',
                        'minutes_trend': 'increasing',
                        'confidence_tier': 'moderate',
                    })

            self.assertEqual(result['recommendation'], 'take_it')
            self.assertEqual(result['model_version'], 'pq_v2')
            self.assertIn('back-to-back game', result['red_flags'])
            self.assertIn('cold streak', result['red_flags'])

    def test_predict_pick_quality_invalid_metadata_and_model_error(self):
        from app.services import pick_quality_model

        class _FailingXGBClassifier:
            def load_model(self, _path):
                return None

            def predict_proba(self, _x):
                raise RuntimeError('boom')

        fake_xgboost = SimpleNamespace(XGBClassifier=_FailingXGBClassifier)
        fake_np = SimpleNamespace(array=lambda x: x)

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='bad_meta',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=10,
                val_accuracy=0.5,
                is_active=True,
                metadata_json='{bad',
            ))
            db.session.commit()

            with patch('app.services.pick_quality_model.materialize_model_artifact', return_value='/tmp/model.json'):
                bad_meta_result = pick_quality_model.predict_pick_quality({'projected_edge': 1.0})
            self.assertEqual(bad_meta_result['recommendation'], 'no_model')

            ModelMetadata.query.update({'is_active': False})
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='predict_fail',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=10,
                val_accuracy=0.5,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge"]}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': fake_xgboost, 'numpy': fake_np}):
                with patch('app.services.pick_quality_model.materialize_model_artifact', return_value='/tmp/model.json'):
                    err_result = pick_quality_model.predict_pick_quality({'projected_edge': 1.0})
            self.assertEqual(err_result['recommendation'], 'no_model')

    def test_predict_pick_quality_caution_band(self):
        """Probabilities in caution band should return caution (not take_it)."""
        from app.services import pick_quality_model

        class _FakeXGBClassifier:
            def load_model(self, _path):
                return None

            def predict_proba(self, _x):
                return [[0.42, 0.60]]

        fake_xgboost = SimpleNamespace(XGBClassifier=_FakeXGBClassifier)
        fake_np = SimpleNamespace(array=lambda x: x)

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='pq_caution',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=25,
                val_accuracy=0.62,
                is_active=True,
                metadata_json=(
                    '{"feature_names":["projected_edge","player_trend","minutes_trend","confidence_tier_num","injury_returning"],'
                    '"probability_shrink":1.0,"take_it_threshold":0.62,"caution_threshold":0.54}'
                ),
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': fake_xgboost, 'numpy': fake_np}):
                with patch('app.services.pick_quality_model.materialize_model_artifact', return_value='/tmp/model.json'):
                    result = pick_quality_model.predict_pick_quality({'projected_edge': 1.0})

            self.assertEqual(result['recommendation'], 'caution')
            self.assertAlmostEqual(result['win_probability'], 0.6, places=2)

    def test_predict_pick_quality_bias_correction_applied(self):
        """Positive calibration_bias should lower final win probability."""
        from app.services import pick_quality_model

        class _FakeXGBClassifier:
            def load_model(self, _path):
                return None

            def predict_proba(self, _x):
                return [[0.30, 0.66]]

        fake_xgboost = SimpleNamespace(XGBClassifier=_FakeXGBClassifier)
        fake_np = SimpleNamespace(array=lambda x: x)

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='pq_bias',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=25,
                val_accuracy=0.62,
                is_active=True,
                metadata_json=(
                    '{"feature_names":["projected_edge","player_trend","minutes_trend","confidence_tier_num","injury_returning"],'
                    '"probability_shrink":1.0,"calibration_bias":0.05,"take_it_threshold":0.62,"caution_threshold":0.54}'
                ),
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': fake_xgboost, 'numpy': fake_np}):
                with patch('app.services.pick_quality_model.materialize_model_artifact', return_value='/tmp/model.json'):
                    result = pick_quality_model.predict_pick_quality({'projected_edge': 1.0})

            # 0.66 raw - 0.05 bias => ~0.61
            self.assertAlmostEqual(result['win_probability'], 0.61, places=2)
            self.assertEqual(result['recommendation'], 'caution')


# ═══════════════════════════════════════════════════════════════════════════
# cli tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCLI(BaseTestCase):
    """Tests for Flask CLI commands in app/cli.py."""

    def _runner(self):
        return self.app.test_cli_runner(mix_stderr=False)

    @patch('app.services.scheduler.refresh_player_stats')
    def test_refresh_stats(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['refresh-stats'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Refreshing player stats', result.output)
        self.assertIn('Done', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.refresh_defense_data')
    def test_refresh_defense(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['refresh-defense'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Refreshing defense data', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.refresh_injury_reports')
    def test_refresh_injuries(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['refresh-injuries'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Refreshing injury reports', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.run_projections')
    def test_run_projections(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['run-projections'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Running projections', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.resolve_and_grade')
    def test_grade_bets(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['grade-bets'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Grading bets', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.retrain_models')
    def test_retrain(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['retrain'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Retraining models', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.generate_daily_auto_picks')
    def test_generate_auto_picks(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['generate-auto-picks'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Generating daily auto picks', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.scheduler.bootstrap_pick_quality_examples', return_value={'created': 25})
    def test_bootstrap_pick_quality(self, mock_fn):
        runner = self._runner()
        result = runner.invoke(args=['bootstrap-pick-quality', '--target', '50'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Bootstrapping hidden pick-quality training examples', result.output)
        self.assertIn('Bootstrap result', result.output)
        mock_fn.assert_called_once()

    @patch('app.services.pick_quality_model.train_pick_quality_model', return_value={'ok': 1})
    @patch('app.services.scheduler.bootstrap_pick_quality_examples', return_value={'created': 100})
    def test_bootstrap_pick_quality_with_train(self, mock_bootstrap, mock_train):
        runner = self._runner()
        result = runner.invoke(args=['bootstrap-pick-quality', '--train-after'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Training pick-quality model', result.output)
        mock_bootstrap.assert_called_once()
        mock_train.assert_called_once()

    def test_data_quality_report(self):
        runner = self._runner()
        result = runner.invoke(args=['data_quality_report'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Data Quality Report ===', result.output)
        self.assertIn('=== PlayerGameLog ===', result.output)
        self.assertIn('=== Context Tables ===', result.output)
        self.assertIn('=== Scheduler/Jobs ===', result.output)
        self.assertIn('=== Verdict ===', result.output)

    @patch('app.services.pick_quality_model.get_calibration_report')
    def test_model_calibration_report(self, mock_report):
        mock_report.return_value = {
            'model_version': 'pick_quality_nba_2026-02-28',
            'total_rows': 120,
            'evaluated': 100,
            'no_model_count': 20,
            'wins': 54,
            'losses': 46,
            'win_rate': 0.54,
            'avg_pred': 0.56,
            'overconfidence_gap': 0.02,
            'brier': 0.2421,
            'logloss': 0.6812,
            'recommendation_counts': {
                'take_it': 70,
                'caution': 10,
                'skip': 20,
                'no_model': 20,
            },
            'bins': [
                {'range': '0.40-0.60', 'count': 60, 'avg_pred': 0.55, 'win_rate': 0.53, 'gap': 0.02},
                {'range': '0.60-0.80', 'count': 40, 'avg_pred': 0.67, 'win_rate': 0.65, 'gap': 0.02},
            ],
        }
        runner = self._runner()
        result = runner.invoke(args=['model_calibration_report', '--limit', '100', '--bins', '5'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Model Calibration Report (Model 2) ===', result.output)
        self.assertIn('Overconfidence gap (pred - actual)', result.output)
        self.assertIn('=== Calibration Bins ===', result.output)
        self.assertIn('=== Verdict ===', result.output)

    @patch('app.services.market_recommender.evaluate_market_models')
    def test_market_model_report(self, mock_report):
        mock_report.return_value = {
            'rows_scanned': 180,
            'policy_used': {
                'moneyline': {'min_edge': 0.03, 'min_confidence': 0.55},
                'total_ou': {'min_edge': 0.06, 'min_confidence': 0.56},
            },
            'markets': {
                'moneyline': {
                    'rows': 160,
                    'accuracy': 0.61,
                    'brier': 0.23,
                    'logloss': 0.66,
                    'avg_pred': 0.55,
                    'actual_rate': 0.53,
                    'overconfidence_gap': 0.02,
                    'recommended_bets': 44,
                    'recommended_bet_rate': 0.275,
                    'recommended_hit_rate': 0.59,
                    'train_val_accuracy': 0.64,
                    'accuracy_delta': -0.03,
                    'train_val_logloss': 0.62,
                    'logloss_delta': 0.04,
                    'bins': [{'range': '0.40-0.60', 'count': 40, 'avg_pred': 0.53, 'win_rate': 0.52, 'gap': 0.01}],
                },
                'total_ou': {
                    'rows': 150,
                    'accuracy': 0.58,
                    'brier': 0.24,
                    'logloss': 0.68,
                    'avg_pred': 0.54,
                    'actual_rate': 0.5,
                    'overconfidence_gap': 0.04,
                    'recommended_bets': 31,
                    'recommended_bet_rate': 0.2067,
                    'recommended_hit_rate': 0.55,
                    'train_val_accuracy': 0.6,
                    'accuracy_delta': -0.02,
                    'train_val_logloss': 0.65,
                    'logloss_delta': 0.03,
                    'bins': [{'range': '0.60-0.80', 'count': 30, 'avg_pred': 0.64, 'win_rate': 0.6, 'gap': 0.04}],
                },
            },
        }
        runner = self._runner()
        result = runner.invoke(args=['market-model-report', '--days', '90', '--bins', '5'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Market Model Report', result.output)
        self.assertIn('--- moneyline ---', result.output)
        self.assertIn('--- total_ou ---', result.output)
        self.assertIn('=== Verdict ===', result.output)

    @patch('app.services.market_recommender.tune_market_thresholds')
    def test_market_threshold_tune(self, mock_tune):
        mock_tune.return_value = {
            'policy': {
                'moneyline': {'min_edge': 0.03, 'min_confidence': 0.58},
                'total_ou': {'min_edge': 0.06, 'min_confidence': 0.59},
            },
            'selected': {
                'moneyline': {
                    'selected': {'min_edge': 0.03, 'min_confidence': 0.58},
                    'score': 0.1234,
                    'metrics': {'recommended_bets': 40, 'roi_per_bet': 0.08, 'closing_edge_proxy': 0.05, 'overconfidence_gap': 0.01},
                },
                'total_ou': {
                    'selected': {'min_edge': 0.06, 'min_confidence': 0.59},
                    'score': 0.1102,
                    'metrics': {'recommended_bets': 38, 'roi_per_bet': 0.06, 'closing_edge_proxy': 0.04, 'overconfidence_gap': 0.02},
                },
            },
            'applied': True,
            'apply_result': {'updated_models': ['market_moneyline_nba', 'market_total_ou_nba']},
        }
        runner = self._runner()
        result = runner.invoke(args=['market-threshold-tune', '--days', '90', '--min-bets', '20'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Market Threshold Tune', result.output)
        self.assertIn('Selected policy:', result.output)
        self.assertIn('--- moneyline ---', result.output)
        self.assertIn('=== Apply ===', result.output)

    @patch('app.services.market_recommender.guard_market_recommendations')
    def test_market_guard_check(self, mock_guard):
        mock_guard.return_value = {
            'decisions': {
                'moneyline': {
                    'decision': 'disable', 'drift_breach': True, 'roi_breach': True,
                    'recommended_bets': 24, 'accuracy_delta': -0.07, 'roi_per_bet': -0.12,
                },
                'total_ou': {
                    'decision': 'keep_enabled', 'drift_breach': False, 'roi_breach': False,
                    'recommended_bets': 30, 'accuracy_delta': 0.01, 'roi_per_bet': 0.05,
                },
            },
            'applied': True,
            'apply_result': {'moneyline': {'enabled': False}, 'total_ou': {'enabled': True}},
        }
        runner = self._runner()
        result = runner.invoke(args=['market-guard-check', '--days', '60', '--apply'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Market Guard Check', result.output)
        self.assertIn('Decision=disable', result.output)
        self.assertIn('=== Apply ===', result.output)

    @patch('app.services.market_recommender.walkforward_market_report')
    def test_market_walkforward_report(self, mock_walk):
        mock_walk.return_value = {
            'rows_scanned': 120,
            'policy_used': {'moneyline': {'min_edge': 0.03, 'min_confidence': 0.55}},
            'markets': {
                'moneyline': {
                    'summary': {'avg_accuracy': 0.59, 'folds': 3},
                    'folds': [
                        {
                            'test_start': '2026-02-01', 'test_end': '2026-02-14',
                            'rows': 20, 'accuracy': 0.6, 'brier': 0.24,
                            'recommended_bets': 8, 'roi_per_bet': 0.04,
                        },
                    ],
                },
                'total_ou': {'summary': {'avg_accuracy': 0.57, 'folds': 3}, 'folds': []},
            },
        }
        runner = self._runner()
        result = runner.invoke(args=['market-walkforward-report', '--days', '120'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Market Walk-Forward Report', result.output)
        self.assertIn('--- moneyline ---', result.output)
        self.assertIn('Summary:', result.output)

    @patch('app.services.market_recommender.run_market_governance')
    def test_market_governance_run(self, mock_governance):
        mock_governance.return_value = {
            'tune': {'selected': {'moneyline': {'min_edge': 0.03, 'min_confidence': 0.58}}},
            'guard': {'decisions': {'moneyline': {'decision': 'disable'}}},
            'walkforward': {'markets': {'moneyline': {'summary': {'avg_accuracy': 0.58}}, 'total_ou': {'summary': {'avg_accuracy': 0.56}}}},
        }
        runner = self._runner()
        result = runner.invoke(args=['market-governance-run', '--days', '120', '--apply'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Market Governance Run', result.output)
        self.assertIn('Tune summary:', result.output)
        self.assertIn('Guard summary:', result.output)
        self.assertIn('Walk-forward summary:', result.output)

    @patch('app.services.nba_service.backfill_game_snapshots')
    def test_backfill_game_snapshots_cli(self, mock_backfill):
        mock_backfill.return_value = {
            'scanned_days': 10, 'scanned_games': 42, 'created': 10,
            'updated': 5, 'ou_filled': 3, 'moneyline_filled': 2,
        }
        runner = self._runner()
        result = runner.invoke(args=['backfill-game-snapshots', '--start-date', '2026-02-01', '--end-date', '2026-02-10'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Backfill result:', result.output)
        self.assertIn('scanned_days=10', result.output)

    @patch('app.services.market_recommender.walkforward_market_report')
    def test_market_data_coverage_report_cli(self, mock_wf):
        mock_wf.return_value = {'error': 'no_folds'}
        runner = self._runner()
        result = runner.invoke(args=['market-data-coverage-report', '--days', '180'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('=== Market Data Coverage', result.output)
        self.assertIn('Walk-forward feasibility: NOT READY', result.output)


# ═══════════════════════════════════════════════════════════════════════════
# health/readiness endpoint tests
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint(BaseTestCase):
    """Tests for the /health endpoint."""

    def test_health_returns_200(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')

    def test_health_returns_200_when_db_down(self):
        with patch.object(db.session, 'execute', side_effect=Exception("DB down")):
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
        with patch.object(db.session, 'execute', side_effect=Exception("DB down")):
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


# ═══════════════════════════════════════════════════════════════════════════
# Unit 1: ValueDetector Model 2 integration
# ═══════════════════════════════════════════════════════════════════════════

class TestValueDetectorModel2Integration(BaseTestCase):
    """Tests for Model 2 integration in score_prop()."""

    def _make_engine_with_proj(self, projection=25.0, std_dev=4.0, games=20,
                                confidence='medium', z_score=0.0, context_notes=None):
        engine = MagicMock()
        engine.project_stat.return_value = {
            'projection': projection,
            'std_dev': std_dev,
            'games_played': games,
            'confidence': confidence,
            'context_notes': context_notes or [],
            'z_score': z_score,
            'projection_source': 'heuristic',
            'breakdown': {'season_avg': projection},
        }
        return engine

    def test_score_prop_returns_win_probability_key(self):
        """score_prop always returns win_probability (None when Model 2 unavailable)."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj()
        detector = ValueDetector(engine=engine)
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       side_effect=Exception('no model')):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 24.5, -110, -110,
                )
        # win_probability is None when Model 2 is unavailable (exception swallowed)
        self.assertIn('win_probability', result)
        self.assertIn('pick_quality_recommendation', result)
        self.assertIsNone(result['win_probability'])
        self.assertEqual(result['pick_quality_recommendation'], 'no_model')

    def test_score_prop_model2_downgrades_moderate_to_slight(self):
        """confidence_tier is downgraded from moderate to slight when win_prob < 0.42."""
        from app.services.value_detector import ValueDetector
        # Use confidence='low' so even large edges don't become 'strong'
        # Then edge lands in 'moderate' range
        engine = self._make_engine_with_proj(projection=25.0, games=20, confidence='low')
        detector = ValueDetector(engine=engine)

        fake_quality = {
            'win_probability': 0.35,
            'recommendation': 'skip',
            'red_flags': [],
            'model_version': 'v1',
        }
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       return_value=fake_quality):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 22.5, -110, -110,
                )
        # With confidence='low' (not in STRONG_CONFIDENCE_LEVELS), the tier is 'moderate'
        # Model 2 downgrades 'moderate' to 'slight' because win_prob < 0.42
        self.assertEqual(result['win_probability'], 0.35)
        self.assertEqual(result['pick_quality_recommendation'], 'skip')
        # The tier should have been downgraded from moderate to slight
        self.assertIn(result['confidence_tier'], ('slight', 'no_edge'))

    def test_score_prop_model2_adds_quality_note_high_prob(self):
        """High win_prob adds ML quality context note."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj(projection=28.0, games=20, confidence='high')
        detector = ValueDetector(engine=engine)

        fake_quality = {
            'win_probability': 0.72,
            'recommendation': 'take_it',
            'red_flags': [],
            'model_version': 'v1',
        }
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       return_value=fake_quality):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 20.5, -110, -110,
                )
        self.assertEqual(result['win_probability'], 0.72)
        self.assertTrue(any('ML quality' in n for n in result['context_notes']))

    def test_score_prop_model2_adds_caution_note_low_prob(self):
        """Low win_prob adds ML caution context note."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj(projection=22.0, games=20, confidence='medium')
        detector = ValueDetector(engine=engine)

        fake_quality = {
            'win_probability': 0.30,
            'recommendation': 'skip',
            'red_flags': ['high variance'],
            'model_version': 'v1',
        }
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       return_value=fake_quality):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 22.5, -110, -110,
                )
        self.assertEqual(result['win_probability'], 0.30)
        self.assertTrue(any('ML caution' in n for n in result['context_notes']))

    def test_score_prop_b2b_detected_from_context_notes(self):
        """B2B flag is correctly inferred from projection context_notes (fallback path)."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj(
            projection=22.0, games=20, confidence='medium',
            context_notes=['back-to-back (-8%)', 'away game (-3%)']
        )
        detector = ValueDetector(engine=engine)

        captured_ctx = {}

        def capture_ctx(ctx, **kwargs):
            captured_ctx.update(ctx)
            return {'win_probability': 0.55, 'recommendation': 'caution', 'red_flags': []}

        with self.app.app_context():
            # Force fallback path (no player_id) so B2B is read from context_notes.
            with patch('app.services.value_detector.find_player_id', return_value=''), \
                 patch('app.services.value_detector.predict_pick_quality',
                       side_effect=capture_ctx):
                detector.score_prop('LeBron James', 'player_points', 22.5, -110, -110)

        self.assertTrue(captured_ctx.get('back_to_back'))


# ═══════════════════════════════════════════════════════════════════════════
# Unit 2: Model 2 calibration + cold-start
# ═══════════════════════════════════════════════════════════════════════════

class TestPickQualityModelCalibration(BaseTestCase):
    """Tests for calibration, cold-start threshold, and local fallback."""

    def test_min_resolved_picks_is_100(self):
        from app.services import pick_quality_model
        self.assertEqual(pick_quality_model.MIN_RESOLVED_PICKS, 100)

    def test_find_local_model_fallback_no_files(self):
        """Returns None when no local model files exist."""
        from app.services.pick_quality_model import _find_local_model_fallback
        with patch('glob.glob', return_value=[]):
            result = _find_local_model_fallback('pick_quality_nba')
        self.assertIsNone(result)

    def test_find_local_model_fallback_returns_latest_pkl(self):
        """Returns the most recent .pkl file when available."""
        from app.services.pick_quality_model import _find_local_model_fallback
        fake_files = ['/models/pick_quality_nba_2026-02-28.pkl']
        with patch('glob.glob', return_value=fake_files):
            result = _find_local_model_fallback('pick_quality_nba')
        self.assertEqual(result, fake_files[0])

    def test_find_local_model_fallback_falls_back_to_json(self):
        """Falls back to .json when no .pkl exists."""
        from app.services.pick_quality_model import _find_local_model_fallback

        def fake_glob(pattern):
            if pattern.endswith('.pkl'):
                return []
            return ['/models/pick_quality_nba_2026-02-28.json']

        with patch('glob.glob', side_effect=fake_glob):
            result = _find_local_model_fallback('pick_quality_nba')
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith('.json'))

    def test_predict_uses_local_fallback_when_s3_fails(self):
        """predict_pick_quality uses local fallback when S3 returns None."""
        from app.services import pick_quality_model

        class _FakeModel:
            def predict_proba(self, x):
                return [[0.4, 0.6]]

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='s3_fail_v1',
                file_path='s3://bucket/model.pkl',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge","player_trend","minutes_trend","confidence_tier_num","injury_returning"]}',
            ))
            db.session.commit()

            with patch('app.services.pick_quality_model.materialize_model_artifact',
                       return_value=None):
                with patch.object(pick_quality_model, '_find_local_model_fallback',
                                  return_value='/tmp/fallback_model.pkl'):
                    with patch('builtins.open', MagicMock()):
                        with patch('joblib.load', return_value=_FakeModel()):
                            result = pick_quality_model.predict_pick_quality(
                                {'projected_edge': 0.1}
                            )
            # Model was loaded via fallback → win_probability should be a real prediction
            self.assertIsNotNone(result)
            self.assertIn('win_probability', result)

    def test_predict_loads_pkl_via_joblib(self):
        """predict_pick_quality loads .pkl files using joblib.load."""
        from app.services import pick_quality_model

        class _FakeModel:
            def predict_proba(self, x):
                return [[0.4, 0.6]]

        fake_np = SimpleNamespace(array=lambda x: x)

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='pkl_v1',
                file_path='/tmp/model.pkl',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge","player_trend","minutes_trend","confidence_tier_num","injury_returning"]}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'numpy': fake_np}):
                with patch('app.services.pick_quality_model.materialize_model_artifact',
                           return_value='/tmp/model.pkl'):
                    with patch('joblib.load', return_value=_FakeModel()):
                        result = pick_quality_model.predict_pick_quality(
                            {'projected_edge': 0.1}
                        )
            self.assertIn('win_probability', result)
            self.assertNotEqual(result['recommendation'], 'no_model')

    def test_model_runtime_probe_no_active_model(self):
        """Probe reports no_active_model when metadata is absent."""
        from app.services import pick_quality_model

        class _FakeXGB:
            def load_model(self, path):
                return None

        with patch.dict(sys.modules, {'xgboost': SimpleNamespace(XGBClassifier=_FakeXGB)}):
            with self.app.app_context():
                probe = pick_quality_model.get_model_runtime_probe()
        self.assertFalse(probe['active_model_found'])
        self.assertFalse(probe['model_loadable'])
        self.assertEqual(probe['reason'], 'no_active_model')

    def test_model_runtime_probe_artifact_unavailable(self):
        """Probe reports artifact_unavailable when no configured/fallback file exists."""
        from app.services import pick_quality_model

        class _FakeXGB:
            def load_model(self, path):
                return None

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='probe_v1',
                file_path='s3://bucket/model.pkl',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge"]}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': SimpleNamespace(XGBClassifier=_FakeXGB)}):
                with patch('app.services.pick_quality_model.materialize_model_artifact', return_value=None):
                    with patch('app.services.pick_quality_model._find_local_model_fallback', return_value=None):
                        probe = pick_quality_model.get_model_runtime_probe()

        self.assertTrue(probe['active_model_found'])
        self.assertFalse(probe['model_loadable'])
        self.assertEqual(probe['reason'], 'artifact_unavailable')

    def test_model_runtime_probe_loadable_via_configured_pkl(self):
        """Probe marks model loadable when configured .pkl artifact can be joblib-loaded."""
        from app.services import pick_quality_model

        class _FakeXGB:
            def load_model(self, path):
                return None

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='probe_v2',
                file_path='s3://bucket/model.pkl',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge"]}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': SimpleNamespace(XGBClassifier=_FakeXGB)}):
                with patch('app.services.pick_quality_model.materialize_model_artifact',
                           return_value='/tmp/pick_quality_nba_2026-03-15.pkl'):
                    with patch('joblib.load', return_value=object()):
                        probe = pick_quality_model.get_model_runtime_probe()

        self.assertTrue(probe['active_model_found'])
        self.assertTrue(probe['model_loadable'])
        self.assertEqual(probe['artifact_source'], 'configured_path')
        self.assertEqual(probe['reason'], 'ok')

    def test_model_runtime_probe_reports_load_error(self):
        """Probe returns load_error when artifact exists but fails to deserialize."""
        from app.services import pick_quality_model

        class _FakeXGB:
            def load_model(self, path):
                return None

        with self.app.app_context():
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='probe_v3',
                file_path='s3://bucket/model.pkl',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.6,
                is_active=True,
                metadata_json='{"feature_names":["projected_edge"]}',
            ))
            db.session.commit()

            with patch.dict(sys.modules, {'xgboost': SimpleNamespace(XGBClassifier=_FakeXGB)}):
                with patch('app.services.pick_quality_model.materialize_model_artifact',
                           return_value='/tmp/pick_quality_nba_2026-03-15.pkl'):
                    with patch('joblib.load', side_effect=ValueError('broken')):
                        probe = pick_quality_model.get_model_runtime_probe()

        self.assertTrue(probe['active_model_found'])
        self.assertFalse(probe['model_loadable'])
        self.assertIn('load_error', probe['reason'])


# ═══════════════════════════════════════════════════════════════════════════
# Unit 3: Model 1 steals/blocks in STAT_TYPES and ML_STAT_MAP
# ═══════════════════════════════════════════════════════════════════════════

class TestModel1StealsBocks(BaseTestCase):
    """Tests for steals/blocks in Model 1 configuration."""

    def test_stat_types_includes_steals_and_blocks(self):
        from app.services.ml_model import STAT_TYPES
        self.assertIn('player_steals', STAT_TYPES)
        self.assertIn('player_blocks', STAT_TYPES)

    def test_stat_key_map_includes_steals_and_blocks(self):
        from app.services.ml_model import STAT_KEY_MAP
        self.assertEqual(STAT_KEY_MAP['player_steals'], 'stl')
        self.assertEqual(STAT_KEY_MAP['player_blocks'], 'blk')

    def test_ml_stat_map_in_projection_engine_includes_steals_blocks(self):
        from app.services.projection_engine import ML_STAT_MAP
        self.assertIn('player_steals', ML_STAT_MAP)
        self.assertIn('player_blocks', ML_STAT_MAP)
        self.assertEqual(ML_STAT_MAP['player_steals'], 'player_steals')
        self.assertEqual(ML_STAT_MAP['player_blocks'], 'player_blocks')

    def test_train_model_metadata_includes_cv_fields(self):
        """train_model source contains cv_mean_mae/cv_std_mae metadata keys."""
        import inspect
        from app.services import ml_model
        source = inspect.getsource(ml_model.train_model)
        self.assertIn('cv_mean_mae', source)
        self.assertIn('cv_std_mae', source)
        self.assertIn('TimeSeriesSplit', source)
        self.assertIn('early_stopping_rounds', source)


# ═══════════════════════════════════════════════════════════════════════════
# Unit 4: CLI drift_report + model_status rolling win rate
# ═══════════════════════════════════════════════════════════════════════════

class TestCLIDriftReport(BaseTestCase):
    """Tests for flask drift_report CLI command."""

    def _runner(self):
        return self.app.test_cli_runner(mix_stderr=False)

    def test_drift_report_no_data(self):
        """drift_report outputs message when no resolved bets exist."""
        runner = self._runner()
        result = runner.invoke(args=['drift_report'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Drift Report', result.output)
        self.assertIn('No resolved bets', result.output)

    def test_drift_report_with_bets_no_model(self):
        """drift_report shows rolling win rate without model comparison."""
        with self.app.app_context():
            user = make_user('drift1', 'drift1@ex.com')
            db.session.add(user)
            db.session.commit()
            for i in range(5):
                bet = make_bet(user.id, outcome='win' if i < 3 else 'lose',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.commit()

        runner = self._runner()
        result = runner.invoke(args=['drift_report'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Rolling win rate', result.output)
        self.assertIn('No active pick_quality_nba', result.output)

    def test_drift_report_detects_drift(self):
        """drift_report outputs DRIFT DETECTED when delta > 5%."""
        with self.app.app_context():
            user = make_user('drift2', 'drift2@ex.com')
            db.session.add(user)
            db.session.commit()
            # 10 wins out of 10 → 100% rolling rate vs 55% model accuracy → drift
            for i in range(10):
                bet = make_bet(user.id, outcome='win',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='drift_v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.55,
                is_active=True,
            ))
            db.session.commit()

        runner = self._runner()
        result = runner.invoke(args=['drift_report'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('DRIFT DETECTED', result.output)

    def test_drift_report_no_drift(self):
        """drift_report outputs OK when rolling rate is within 5% of val_accuracy."""
        with self.app.app_context():
            user = make_user('drift3', 'drift3@ex.com')
            db.session.add(user)
            db.session.commit()
            # 6 wins out of 10 → 60% rolling rate vs 58% model accuracy → OK
            for i in range(10):
                bet = make_bet(user.id, outcome='win' if i < 6 else 'lose',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='nodrift_v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.58,
                is_active=True,
            ))
            db.session.commit()

        runner = self._runner()
        result = runner.invoke(args=['drift_report'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('OK', result.output)

    def test_model_status_shows_30d_rolling_rate(self):
        """model_status includes 30-day rolling win rate section."""
        with self.app.app_context():
            user = make_user('ms1', 'ms1@ex.com')
            db.session.add(user)
            db.session.commit()
            for i in range(4):
                bet = make_bet(user.id, outcome='win' if i < 3 else 'lose',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.commit()

        runner = self._runner()
        result = runner.invoke(args=['model_status'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('30-day Rolling Win Rate', result.output)
        self.assertIn('Rolling win rate', result.output)


# ═══════════════════════════════════════════════════════════════════════════
# Unit 5: Scheduler drift monitoring job
# ═══════════════════════════════════════════════════════════════════════════

class TestSchedulerDriftJob(BaseTestCase):
    """Tests for check_model_drift() scheduler function."""

    def test_check_model_drift_no_data(self):
        """check_model_drift logs info and returns when no resolved bets."""
        from app.services import scheduler as sched
        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched.check_model_drift()  # should not raise
        # No JobLog entries created (no drift to report)
        with self.app.app_context():
            drift_logs = JobLog.query.filter_by(job_name='drift_check').all()
            self.assertEqual(len(drift_logs), 0)

    def test_check_model_drift_no_model_metadata(self):
        """check_model_drift logs info when no active model exists."""
        from app.services import scheduler as sched
        with self.app.app_context():
            user = make_user('dm1', 'dm1@ex.com')
            db.session.add(user)
            db.session.commit()
            for i in range(5):
                bet = make_bet(user.id, outcome='win' if i < 3 else 'lose',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.commit()

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched.check_model_drift()

        with self.app.app_context():
            drift_logs = JobLog.query.filter_by(job_name='drift_check').all()
            self.assertEqual(len(drift_logs), 0)  # no warning logged

    def test_check_model_drift_logs_warn_on_large_drift(self):
        """check_model_drift creates JobLog warning when delta > 4%."""
        from app.services import scheduler as sched
        with self.app.app_context():
            user = make_user('dm2', 'dm2@ex.com')
            db.session.add(user)
            db.session.commit()
            # 45/50 wins → 90% rolling rate vs 55% val_accuracy → 35% drift
            for i in range(50):
                bet = make_bet(user.id, outcome='win' if i < 45 else 'lose',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='drift_sched_v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.55,
                is_active=True,
            ))
            db.session.commit()

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched.check_model_drift()

        with self.app.app_context():
            warn_log = JobLog.query.filter_by(job_name='drift_check', status='warn').first()
            self.assertIsNotNone(warn_log)
            self.assertIn('drift', warn_log.message.lower())

    def test_check_model_drift_no_warn_within_threshold(self):
        """check_model_drift does not warn when delta ≤ 4%."""
        from app.services import scheduler as sched
        clean_ctx = json.dumps({
            'opp_defense_rating': 110.0, 'opp_pace': 99.5, 'opp_matchup_adj': 1.02,
        })
        with self.app.app_context():
            user = make_user('dm3', 'dm3@ex.com')
            db.session.add(user)
            db.session.commit()
            # 30/50 wins → 60% rolling rate vs 58% val_accuracy → 2% drift (OK)
            for i in range(50):
                bet = make_bet(user.id, outcome='win' if i < 30 else 'lose',
                               match_date=datetime.now(timezone.utc))
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json=clean_ctx))
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='nodrift_sched_v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.58,
                is_active=True,
            ))
            db.session.commit()

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched.check_model_drift()

        with self.app.app_context():
            warn_logs = JobLog.query.filter_by(job_name='drift_check', status='warn').all()
            self.assertEqual(len(warn_logs), 0)

    def test_check_model_drift_excludes_bootstrap_bets(self):
        """check_model_drift ignores AUTO_BOOTSTRAP_HIDDEN bets (synthetic training data)."""
        from app.services import scheduler as sched
        with self.app.app_context():
            user = make_user('dm4', 'dm4@ex.com')
            db.session.add(user)
            db.session.commit()
            # All bootstrap bets (100% wins) — would trigger drift if counted
            for i in range(10):
                bet = make_bet(
                    user.id,
                    outcome='win',
                    source='auto_generated',
                    notes='AUTO_BOOTSTRAP_HIDDEN:model2',
                    match_date=datetime.now(timezone.utc),
                )
                db.session.add(bet)
                db.session.flush()
                db.session.add(PickContext(bet_id=bet.id, context_json='{}'))
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='boot_test_v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.55,
                is_active=True,
            ))
            db.session.commit()

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched.check_model_drift()

        with self.app.app_context():
            # No warn because bootstrap bets are excluded → no real bets to compare
            warn_logs = JobLog.query.filter_by(job_name='drift_check', status='warn').all()
            self.assertEqual(len(warn_logs), 0)

    def test_drift_check_job_registered_in_scheduler(self):
        """init_scheduler registers a weekly drift_check job."""
        from app.services import scheduler as sched_module

        class FakeScheduler:
            def __init__(self):
                self.running = False
                self.jobs = []
                self.started = False

            def add_job(self, func, trigger, id=None, replace_existing=None):
                self.jobs.append(id)

            def start(self):
                self.started = True

            def get_jobs(self):
                return self.jobs

        fake = FakeScheduler()
        with patch.object(sched_module, 'scheduler', fake):
            with patch.object(sched_module, 'CronTrigger', side_effect=lambda **kw: kw):
                with patch.object(sched_module, '_acquire_scheduler_lock', return_value=True):
                    sched_module.init_scheduler(self.app)

        self.assertIn('drift_check', fake.jobs)
        self.assertIn('market_governance', fake.jobs)
        self.assertIn('snapshot_backfill', fake.jobs)
        self.assertIn('market_coverage_audit', fake.jobs)


class Phase1FeatureBuilderTest(BaseTestCase):
    """Tests for the Phase 1.1 expanded feature engineering additions.

    Covers:
    - extract_opp_abbr (home and away formats)
    - compute_days_rest_from_logs
    - compute_schedule_density
    - compute_opp_history
    - FEATURE_KEYS completeness
    - build_ml_features_from_history with Phase 1 params
    - _build_defense_lookup and _build_game_total_lookup (ml_model helpers)
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_log(self, game_date=None, matchup='LAL vs. BOS', pts=20.0,
                  reb=5.0, ast=5.0, home_away='home', team_abbr='LAL', **kwargs):
        from types import SimpleNamespace
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
    # compute_days_rest_from_logs
    # ------------------------------------------------------------------

    def test_days_rest_normal(self):
        from app.services.ml_feature_builder import compute_days_rest_from_logs
        logs = [self._make_log(game_date=date(2026, 1, 10))]
        self.assertEqual(compute_days_rest_from_logs(logs, date(2026, 1, 12)), 2.0)

    def test_days_rest_back_to_back(self):
        from app.services.ml_feature_builder import compute_days_rest_from_logs
        logs = [self._make_log(game_date=date(2026, 1, 10))]
        self.assertEqual(compute_days_rest_from_logs(logs, date(2026, 1, 11)), 1.0)

    def test_days_rest_no_date(self):
        from app.services.ml_feature_builder import compute_days_rest_from_logs
        # current_game_date=None → default 3.0
        self.assertEqual(compute_days_rest_from_logs([], None), 3.0)

    def test_days_rest_no_logs(self):
        from app.services.ml_feature_builder import compute_days_rest_from_logs
        # no logs → default 3.0
        self.assertEqual(compute_days_rest_from_logs([], date(2026, 1, 15)), 3.0)

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
        self.assertEqual(len(FEATURE_KEYS), 37, "Expected 37 feature keys (21 original + 9 Phase 1 + 7 Phase 2)")

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
    # _build_defense_lookup (ml_model helper)
    # ------------------------------------------------------------------

    def test_build_defense_lookup_basic(self):
        from app.services.ml_model import _build_defense_lookup
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
            lookup = _build_defense_lookup()

        self.assertIn('BOS', lookup)
        self.assertAlmostEqual(lookup['BOS']['def_rating'], 108.0)
        self.assertAlmostEqual(lookup['BOS']['pace'], 99.5)

    def test_build_defense_lookup_most_recent_wins(self):
        """Only the most-recent snapshot per team is kept."""
        from app.services.ml_model import _build_defense_lookup
        with self.app.app_context():
            for rating, snap_date in [(110.0, date(2026, 1, 1)), (108.0, date(2026, 1, 20))]:
                db.session.add(TeamDefenseSnapshot(
                    team_id='MIA1', team_name='Miami Heat',
                    team_abbr='MIA', snapshot_date=snap_date,
                    def_rating=rating, pace=100.0,
                    opp_pts_pg=110.0,
                ))
            db.session.commit()
            lookup = _build_defense_lookup()

        self.assertAlmostEqual(lookup['MIA']['def_rating'], 108.0)


if __name__ == '__main__':
    unittest.main()
