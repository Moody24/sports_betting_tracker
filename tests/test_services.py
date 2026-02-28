"""Comprehensive tests for uncovered service modules.

Covers: feature_engine, projection_engine, context_service,
        stats_service, matchup_service, value_detector, cli.
All external API calls are mocked.
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
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

    def test_parse_game_date_invalid_returns_today(self):
        from app.services.stats_service import _parse_game_date
        result = _parse_game_date('not-a-date')
        self.assertEqual(result, date.today())

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
            deleted = prune_expired_cache()
            self.assertEqual(deleted, 1)

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
        from app.services.context_service import get_days_rest

        def side_effect(url, params=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            date_str = params.get('dates', '') if params else ''
            two_days_ago = (date.today() - timedelta(days=2)).strftime('%Y%m%d')
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
            adj_3pm = get_matchup_adjustment('Celtics', 'player_threes')
            self.assertIsInstance(adj_3pm, float)

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
            ):
                self.assertIn(key, features)
            self.assertGreaterEqual(features['fg_pct_last_10'], 0.0)
            self.assertLessEqual(features['fg_pct_last_10'], 1.0)
            self.assertGreaterEqual(features['ts_pct_last_10'], 0.0)

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
        ):
            self.assertIn(key, sample)

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
            with patch.object(ml_model, '_build_training_data', return_value=(mock_features, mock_targets)):
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
            ]):
                out = ml_model.retrain_all_models()
            self.assertIn('player_points', out)
            self.assertIn('player_threes', out)

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

    def test_refresh_jobs_and_projection_job(self):
        from app.services import scheduler as scheduler_module
        with patch('app.create_app', return_value=self.app):
            with patch('app.services.nba_service.get_todays_games', return_value=[{'id': 'g1'}]):
                with patch('app.services.stats_service.update_player_logs_for_games', return_value=2):
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
                    'edge': 0.09,
                    'edge_over': -0.09,
                    'edge_under': 0.09,
                    'confidence_tier': 'moderate',
                    'projection': 26.0,
                    'games_played': 20,
                    'game_id': 'espn1',
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
                    'edge': 0.08,
                    'edge_over': 0.08,
                    'edge_under': -0.08,
                    'confidence_tier': 'moderate',
                    'projection': 25.0,
                    'games_played': 20,
                    'game_id': 'espn1',
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
        self.assertEqual(len(fake.jobs), 8)


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

            features, targets = pick_quality_model._build_training_data()
            self.assertIsNone(features)
            self.assertIsNone(targets)

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
                    '"confidence_tier": "strong", "injury_returning": false}'
                ),
            ))
            db.session.add(PickContext(
                bet_id=bet2.id,
                context_json=(
                    '{"projected_edge": "bad", "back_to_back": false, '
                    '"player_last5_trend": "cold", "minutes_trend": "decreasing", '
                    '"confidence_tier": "slight", "injury_returning": true}'
                ),
            ))
            db.session.commit()

            with patch.object(pick_quality_model, 'MIN_RESOLVED_PICKS', 2):
                features, targets = pick_quality_model._build_training_data()

            self.assertEqual(len(features), 2)
            self.assertEqual(targets, [1, 0])
            self.assertEqual(features[0]['player_trend'], 1)
            self.assertEqual(features[0]['minutes_trend'], 1)
            self.assertEqual(features[0]['confidence_tier_num'], 3)
            self.assertEqual(features[0]['injury_returning'], 0)
            self.assertEqual(features[1]['projected_edge'], 0.0)
            self.assertEqual(features[1]['player_trend'], -1)

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


if __name__ == '__main__':
    unittest.main()
