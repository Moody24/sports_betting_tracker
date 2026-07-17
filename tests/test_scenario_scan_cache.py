"""Per-scan caching of the scenario signal's DB reads (Plan C efficiency
follow-up).

During one score_all_todays_props scan with USE_SCENARIO_SIGNAL=true the
detector must fetch the ScenarioContextPack once, build live context once
per (player, game), and load 'all'-scope splits once per (player, stat) —
instead of re-querying all three for every scored line. Standalone
score_prop calls (no scan) keep the uncached per-call behavior.
"""

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app import db
from app.models import (
    HistoricalGameLog,
    PlayerGameLog,
    ScenarioContextPack,
    ScenarioSplit,
)
from tests.helpers import BaseTestCase


def _split(player_id, stat='pts', dim1='home_away', bucket1='home',
           shrunk=30.0, n=10, scope='all', dim2=None, bucket2=None):
    return ScenarioSplit(
        sport='nba', player_id=player_id, player_name=f'P{player_id}',
        stat=stat, dim1=dim1, bucket1=bucket1, dim2=dim2, bucket2=bucket2,
        season_scope=scope, n=n, raw_mean=shrunk, shrunk_mean=shrunk,
        baseline_mean=25.0)


class TestAgreementScorePrefetchedSplits(BaseTestCase):

    def test_prefetched_splits_used_instead_of_query(self):
        from app.services.scenario_engine import agreement_score
        with self.app.app_context():
            # DB is empty — a query-based implementation would return (0, 0).
            splits = [_split('1', shrunk=30.0, n=10)]
            score, n = agreement_score(
                '1', 'pts', 25.5, {'home_away': 'home'}, splits=splits)
        self.assertEqual((score, n), (1.0, 1))

    def test_empty_prefetched_splits_short_circuits_query(self):
        from app.services.scenario_engine import agreement_score
        with self.app.app_context():
            # A matching row EXISTS in the DB; splits=[] must still win
            # (empty prefetch is not "please go query").
            db.session.add(_split('1', shrunk=30.0, n=10))
            db.session.commit()
            score, n = agreement_score(
                '1', 'pts', 25.5, {'home_away': 'home'}, splits=[])
        self.assertEqual((score, n), (0.0, 0))

    def test_load_agreement_splits_filters_scope_stat_player(self):
        from app.services.scenario_engine import load_agreement_splits
        with self.app.app_context():
            db.session.add(_split('1', stat='pts', scope='all'))
            db.session.add(_split('1', stat='pts', scope='2025-26',
                                  bucket1='away'))
            db.session.add(_split('1', stat='reb', scope='all'))
            db.session.add(_split('2', stat='pts', scope='all'))
            db.session.commit()
            rows = load_agreement_splits('1', 'pts')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].season_scope, 'all')
        self.assertEqual(rows[0].stat, 'pts')
        self.assertEqual(rows[0].player_id, '1')


class TestBuildLiveContextProvidedPack(BaseTestCase):

    def test_provided_pack_used_without_db_read(self):
        from app.services.live_context import build_live_context
        with self.app.app_context():
            # No ScenarioContextPack row exists — pack-derived dims can
            # only appear (and fresh can only be True) via the injected pack.
            payload = {'team_def_tier': {'BOS': 'top10'},
                       'team_game_poss': {},
                       'pace_bins': None,
                       'total_bins': [200.0, 210.0, 220.0, 230.0]}
            ctx, fresh = build_live_context(
                '55', team_abbr='DEN', opponent_abbr='BOS', is_home=True,
                game_date=date(2026, 2, 1), total=225.0,
                pack=(payload, True))
        self.assertTrue(fresh)
        self.assertEqual(ctx['opp_def_tier'], 'top10')
        self.assertEqual(ctx['total_bucket'], 'high')

    def test_provided_stale_pack_reports_not_fresh(self):
        from app.services.live_context import build_live_context
        with self.app.app_context():
            payload = {'team_def_tier': {'BOS': 'top10'}}
            ctx, fresh = build_live_context(
                '55', team_abbr='DEN', opponent_abbr='BOS', is_home=True,
                game_date=date(2026, 2, 1), pack=(payload, False))
        self.assertFalse(fresh)
        self.assertEqual(ctx['opp_def_tier'], 'top10')


class TestScanScopedScenarioCache(BaseTestCase):

    PLAYERS = (('501', 'E501', 'Cache Player One'),
               ('502', 'E502', 'Cache Player Two'))

    def _seed_fixture(self):
        for pid, espn_id, name in self.PLAYERS:
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id=pid, player_name=name, team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25, reb=6, ast=4, fg3m=2, minutes=33,
                    stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6,
                    fg3a=6))
            for i in range(5):
                db.session.add(HistoricalGameLog(
                    sport='nba', player_id=espn_id, player_name=name,
                    team_abbr='LAL', opp_abbr='BOS',
                    game_id=f'g{espn_id}{i}',
                    game_date=date(2026, 2, 1) + timedelta(days=i * 2),
                    season='2025-26', home_away='home', win_loss='W',
                    starter=True,
                    stats={'pts': 25.0, 'reb': 6.0, 'ast': 4.0},
                    fetched_at=datetime.now(timezone.utc)))
            db.session.add(_split(espn_id, shrunk=30.0, n=10))
        db.session.add(ScenarioContextPack(
            sport='nba',
            payload=json.dumps({'team_def_tier': {}, 'team_game_poss': {},
                                'pace_bins': None, 'total_bins': None}),
            computed_at=datetime.now(timezone.utc)))
        db.session.commit()

    def _mock_games_and_props(self):
        games = [{
            'odds_event_id': 'evt1', 'espn_id': 'espn1',
            'start_time': '2026-02-25T19:00:00Z',
            'home': {'name': 'Lakers'},
            'away': {'name': 'Celtics'},
        }]
        line = {'over_odds': -110, 'under_odds': -110}
        props = {'player_points': [
            {'player': 'Cache Player One', 'line': 25.5, **line},
            {'player': 'Cache Player One', 'line': 26.5, **line},
            {'player': 'Cache Player One', 'line': 24.5, **line},
            {'player': 'Cache Player Two', 'line': 25.5, **line},
        ]}
        return games, props

    def test_scan_fetches_pack_context_and_splits_once_per_key(self):
        from app.services import live_context, scenario_engine
        from app.services.value_detector import ValueDetector

        pid_map = {name: pid for pid, _, name in self.PLAYERS}
        espn_map = {name: espn for _, espn, name in self.PLAYERS}
        games, props = self._mock_games_and_props()

        with self.app.app_context():
            self._seed_fixture()
            detector = ValueDetector()
            with patch.dict('os.environ', {'USE_SCENARIO_SIGNAL': 'true'}), \
                 patch('app.services.nba_service.fetch_player_props_for_event',
                       return_value=props), \
                 patch('app.services.projection_engine.find_player_id',
                       side_effect=pid_map.get), \
                 patch('app.services.player_crosswalk.resolve_espn_id',
                       side_effect=espn_map.get), \
                 patch.object(live_context, 'get_live_pack',
                              wraps=live_context.get_live_pack) as pack_spy, \
                 patch.object(live_context, 'build_live_context',
                              wraps=live_context.build_live_context) as ctx_spy, \
                 patch.object(scenario_engine, 'load_agreement_splits',
                              wraps=scenario_engine.load_agreement_splits
                              ) as splits_spy:
                scores = detector.score_all_todays_props(games=games)

        # All 4 lines scored, each carrying a real scenario signal.
        self.assertEqual(len(scores), 4)
        for s in scores:
            self.assertEqual(s['scenario_matches'], 1)
            self.assertIsNotNone(s['scenario_agreement'])

        # One pack fetch per scan; one context build per (player, game);
        # one splits load per (player, stat) — NOT one of each per line.
        self.assertEqual(pack_spy.call_count, 1)
        self.assertEqual(ctx_spy.call_count, 2)
        self.assertEqual(splits_spy.call_count, 2)

    def test_cache_resets_between_scans(self):
        from app.services import live_context
        from app.services.value_detector import ValueDetector

        pid_map = {name: pid for pid, _, name in self.PLAYERS}
        espn_map = {name: espn for _, espn, name in self.PLAYERS}
        games, props = self._mock_games_and_props()

        with self.app.app_context():
            self._seed_fixture()
            detector = ValueDetector()
            with patch.dict('os.environ', {'USE_SCENARIO_SIGNAL': 'true'}), \
                 patch('app.services.nba_service.fetch_player_props_for_event',
                       return_value=props), \
                 patch('app.services.projection_engine.find_player_id',
                       side_effect=pid_map.get), \
                 patch('app.services.player_crosswalk.resolve_espn_id',
                       side_effect=espn_map.get), \
                 patch.object(live_context, 'get_live_pack',
                              wraps=live_context.get_live_pack) as pack_spy:
                detector.score_all_todays_props(games=games)
                detector.score_all_todays_props(games=games)

        # Second scan must NOT reuse the first scan's memoized pack.
        self.assertEqual(pack_spy.call_count, 2)
