"""Bucket-function tests over a hand-built mini store."""

from datetime import date

import pandas as pd

from app.services.scenario_dimensions import build_context
from tests.helpers import BaseTestCase


def _mini_frame():
    """4 games, 2 teams (LAL/BOS vs GSW), 3 players; hand-checkable."""
    rows = []
    def add(pid, name, team, opp, gid, gdate, ha, starter, pts, reb, ast,
            fga, fta, tov, minutes, team_score, opp_score):
        rows.append(dict(
            player_id=pid, player_name=name, team_abbr=team, opp_abbr=opp,
            game_id=gid, game_date=gdate, season='2025-26', home_away=ha,
            starter=starter, pts=pts, reb=reb, ast=ast, fg3m=1.0,
            fga=fga, fta=fta, tov=tov, minutes=minutes,
            team_score=team_score, opp_score=opp_score))
    # G1 2025-10-21 LAL(H,120) v GSW(110): margin 10 -> normal
    add('1', 'A', 'LAL', 'GSW', 'g1', date(2025, 10, 21), 'HOME', True,
        30, 8, 9, 20, 8, 3, 36, 120, 110)
    add('2', 'B', 'LAL', 'GSW', 'g1', date(2025, 10, 21), 'HOME', False,
        12, 3, 2, 9, 2, 1, 20, 120, 110)
    add('3', 'C', 'GSW', 'LAL', 'g1', date(2025, 10, 21), 'AWAY', True,
        25, 4, 6, 22, 5, 2, 38, 110, 120)
    # G2 2025-10-22 (b2b for player 1) LAL(A,100) @ GSW(118): blowout 18
    add('1', 'A', 'LAL', 'GSW', 'g2', date(2025, 10, 22), 'AWAY', True,
        22, 7, 7, 18, 6, 4, 34, 100, 118)
    add('3', 'C', 'GSW', 'LAL', 'g2', date(2025, 10, 22), 'HOME', True,
        31, 5, 8, 24, 7, 1, 37, 118, 100)
    # G3 2026-01-15 mid-season, close game margin 3; player 2 absent (teammate ctx)
    add('1', 'A', 'LAL', 'GSW', 'g3', date(2026, 1, 15), 'HOME', True,
        28, 9, 10, 21, 9, 2, 39, 105, 102)
    add('3', 'C', 'GSW', 'LAL', 'g3', date(2026, 1, 15), 'AWAY', True,
        27, 6, 5, 23, 6, 3, 40, 102, 105)
    # G4 2026-03-20 late season
    add('1', 'A', 'LAL', 'GSW', 'g4', date(2026, 3, 20), 'HOME', True,
        35, 10, 11, 25, 10, 2, 41, 130, 112)
    add('2', 'B', 'LAL', 'GSW', 'g4', date(2026, 3, 20), 'HOME', False,
        15, 4, 3, 11, 3, 1, 22, 130, 112)
    add('3', 'C', 'GSW', 'LAL', 'g4', date(2026, 3, 20), 'AWAY', True,
        20, 3, 4, 19, 4, 5, 35, 112, 130)
    return pd.DataFrame(rows)


class TestBuildContext(BaseTestCase):

    def _ctx(self, odds=None):
        from app.services.scenario_dimensions import build_context
        return build_context(_mini_frame(), odds_df=odds)

    def test_pra_home_away_role_segment(self):
        ctx = self._ctx()
        p1g1 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g1')].iloc[0]
        self.assertEqual(p1g1['pra'], 47.0)              # 30+8+9
        self.assertEqual(p1g1['ctx_home_away'], 'HOME')
        self.assertEqual(p1g1['ctx_role'], 'starter')
        self.assertEqual(p1g1['ctx_season_segment'], 'early')
        p1g3 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g3')].iloc[0]
        self.assertEqual(p1g3['ctx_season_segment'], 'mid')
        p1g4 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g4')].iloc[0]
        self.assertEqual(p1g4['ctx_season_segment'], 'late')
        p2 = ctx[(ctx.player_id == '2') & (ctx.game_id == 'g1')].iloc[0]
        self.assertEqual(p2['ctx_role'], 'bench')

    def test_rest_bucket(self):
        ctx = self._ctx()

        def g(pid, gid):
            return ctx[(ctx.player_id == pid)
                       & (ctx.game_id == gid)].iloc[0]
        self.assertEqual(g('1', 'g1')['ctx_rest_bucket'], '3+')   # first game
        self.assertEqual(g('1', 'g2')['ctx_rest_bucket'], '0')    # back-to-back
        self.assertEqual(g('1', 'g3')['ctx_rest_bucket'], '3+')   # months later

    def test_game_script(self):
        ctx = self._ctx()

        def g(gid):
            return ctx[(ctx.player_id == '1')
                       & (ctx.game_id == gid)].iloc[0]['ctx_game_script']
        self.assertEqual(g('g1'), 'normal')     # margin 10
        self.assertEqual(g('g2'), 'blowout')    # margin 18
        self.assertEqual(g('g3'), 'close')      # margin 3

    def test_teammate_context(self):
        # player 1's top-2 teammates on LAL = player 2 (only other LAL player)
        ctx = self._ctx()

        def g(gid):
            return ctx[(ctx.player_id == '1') & (ctx.game_id == gid)
                       ].iloc[0]['ctx_teammate_context']
        self.assertEqual(g('g1'), 'full')          # player 2 played g1
        self.assertEqual(g('g3'), 'shorthanded')   # player 2 absent g3

    def test_opp_def_tier_is_leakage_safe(self):
        # First game of the season has NO prior data -> NaN bucket (excluded)
        ctx = self._ctx()
        p1g1 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g1')].iloc[0]
        self.assertTrue(pd.isna(p1g1['ctx_opp_def_tier']))
        # g2: GSW's prior allowed = 120 (g1). With 2 teams the league table
        # is tiny; assert the bucket is one of the labels, not NaN.
        p1g2 = ctx[(ctx.player_id == '1') & (ctx.game_id == 'g2')].iloc[0]
        self.assertFalse(pd.isna(p1g2['ctx_opp_def_tier']))

    def test_opp_def_tier_labels(self):
        # Priors as of g2 (2025-10-22), each from g1 only:
        #   LAL allowed 110, GSW allowed 120.
        # rank(pct=True) among the two: LAL 0.5, GSW 1.0.
        # bins [0, 1/3, 2/3, 1.0001] -> LAL 'mid', GSW 'bottom10'
        # (more points allowed = worse defense = bottom tier).
        ctx = self._ctx()

        def g(pid):
            return ctx[(ctx.player_id == pid)
                       & (ctx.game_id == 'g2')].iloc[0]['ctx_opp_def_tier']
        self.assertEqual(g('1'), 'bottom10')   # player 1 (LAL) faces GSW
        self.assertEqual(g('3'), 'mid')        # player 3 (GSW) faces LAL

    def test_pace_tier(self):
        # Possessions per game = both teams' fga + 0.44*fta + tov summed:
        #   g1: LAL 29+0.44*10+4 = 37.4;  GSW 22+0.44*5+2 = 26.2  -> 63.60
        #   g2: LAL 18+0.44*6+4 = 24.64;  GSW 24+0.44*7+1 = 28.08 -> 52.72
        #   g3: LAL 21+0.44*9+2 = 26.96;  GSW 23+0.44*6+3 = 28.64 -> 55.60
        #   g4: LAL 36+0.44*13+3 = 44.72; GSW 19+0.44*4+5 = 25.76 -> 70.48
        # qcut tertile edges over [52.72, 55.6, 63.6, 70.48] land exactly
        # on 55.6 and 63.6 (right-inclusive):
        #   g2, g3 -> slow; g1 -> mid; g4 -> fast
        ctx = self._ctx()

        def g(gid):
            return ctx[(ctx.player_id == '1')
                       & (ctx.game_id == gid)].iloc[0]['ctx_pace_tier']
        self.assertEqual(g('g2'), 'slow')
        self.assertEqual(g('g3'), 'slow')
        self.assertEqual(g('g1'), 'mid')
        self.assertEqual(g('g4'), 'fast')

    def test_fav_dog_and_total_from_odds(self):
        odds = pd.DataFrame([
            dict(game_date=date(2025, 10, 21), home_abbr='LAL',
                 away_abbr='GSW', spread=6.5, favored='home', total=220.0),
            dict(game_date=date(2025, 10, 22), home_abbr='GSW',
                 away_abbr='LAL', spread=8.0, favored='home', total=230.0),
            dict(game_date=date(2026, 1, 15), home_abbr='LAL',
                 away_abbr='GSW', spread=3.0, favored='away', total=210.0),
        ])
        ctx = self._ctx(odds=odds)

        def g(pid, gid, col):
            return ctx[(ctx.player_id == pid)
                       & (ctx.game_id == gid)].iloc[0][col]
        self.assertEqual(g('1', 'g1', 'ctx_fav_dog'), 'fav')      # LAL -6.5 home
        self.assertEqual(g('3', 'g1', 'ctx_fav_dog'), 'dog')
        self.assertEqual(g('1', 'g2', 'ctx_fav_dog'), 'dog_big')  # GSW -8
        self.assertEqual(g('1', 'g3', 'ctx_fav_dog'), 'dog')      # away favored 3
        self.assertTrue(pd.isna(g('1', 'g4', 'ctx_fav_dog')))     # no odds row
        # total buckets: tertiles of [220, 230, 210] within season
        self.assertEqual(g('1', 'g3', 'ctx_total_bucket'), 'low')
        self.assertEqual(g('1', 'g2', 'ctx_total_bucket'), 'high')

    def test_dimensions_registry_shape(self):
        from app.services.scenario_dimensions import DIMENSIONS
        self.assertEqual(len(DIMENSIONS), 10)
        self.assertEqual(DIMENSIONS['fav_dog'],
                         ('fav_big', 'fav', 'dog', 'dog_big'))

    def test_pace_tier_single_game_season_does_not_raise(self):
        # A season with a single game can't support 3 pace tertiles.
        # qcut(duplicates='drop') collapses the bin edges and raises
        # ValueError; build_context must instead yield NaN for that row.
        frame = _mini_frame()
        solo = frame[frame['game_id'] == 'g1'].copy()
        solo['season'] = '2099-00'   # isolate as its own season group
        ctx = build_context(solo)
        self.assertTrue(ctx['ctx_pace_tier'].isna().all())

    def test_pace_tier_all_tied_possessions_does_not_raise(self):
        # All rows in the season group have identical possession totals ->
        # qcut can't form distinct bin edges either.
        frame = _mini_frame()
        tied = frame.copy()
        tied['season'] = '2098-00'
        tied['fga'] = 20
        tied['fta'] = 5
        tied['tov'] = 2
        ctx = build_context(tied)
        self.assertTrue(ctx['ctx_pace_tier'].isna().all())

    def test_pace_tier_unaffected_for_normal_multi_game_season(self):
        # Existing 4-game season group still buckets normally alongside an
        # unrelated single-game season present in the same frame.
        frame = _mini_frame()
        solo = frame[frame['game_id'] == 'g1'].copy()
        solo['season'] = '2099-00'
        solo['game_id'] = 'solo1'
        combined = pd.concat([frame, solo], ignore_index=True)
        ctx = build_context(combined)

        def g(gid):
            return ctx[(ctx.player_id == '1')
                       & (ctx.game_id == gid)].iloc[0]['ctx_pace_tier']
        self.assertEqual(g('g2'), 'slow')
        self.assertEqual(g('g3'), 'slow')
        self.assertEqual(g('g1'), 'mid')
        self.assertEqual(g('g4'), 'fast')
        solo_row = ctx[ctx.game_id == 'solo1'].iloc[0]
        self.assertTrue(pd.isna(solo_row['ctx_pace_tier']))

    def test_total_bucket_single_game_season_does_not_raise(self):
        odds = pd.DataFrame([
            dict(game_date=date(2025, 10, 21), home_abbr='LAL',
                 away_abbr='GSW', spread=6.5, favored='home', total=220.0),
        ])
        ctx = self._ctx(odds=odds)
        self.assertTrue(ctx['ctx_total_bucket'].isna().all())

    def test_build_context_without_score_columns_does_not_raise(self):
        # Pre-backfill store: HistoricalGameLog.stats payload has no
        # team_score/opp_score keys yet, so load_frame's DataFrame lacks
        # those columns entirely.
        frame = _mini_frame().drop(columns=['team_score', 'opp_score'])
        ctx = build_context(frame)
        self.assertTrue(ctx['ctx_game_script'].isna().all())
        self.assertTrue(ctx['ctx_opp_def_tier'].isna().all())


class TestLoaders(BaseTestCase):
    """DB-backed loaders: load_frame / load_odds_frame over seeded rows."""

    def _seed(self):
        from app import db
        from app.models import HistoricalGameLog, HistoricalGameOdds

        def log(pid, name, team, opp, gid, gdate, season, ha, starter,
                **stats):
            return HistoricalGameLog(
                sport='nba', player_id=pid, player_name=name,
                team_abbr=team, opp_abbr=opp, game_id=gid, game_date=gdate,
                season=season, home_away=ha, starter=starter, stats=stats)
        db.session.add_all([
            log('1', 'A', 'LAL', 'GSW', 'g1', date(2025, 10, 21),
                '2025-26', 'HOME', True,
                pts=30.0, reb=8.0, ast=9.0, fg3m=1.0, fga=20.0, fta=8.0,
                tov=3.0, minutes=36.0, team_score=120.0, opp_score=110.0),
            log('3', 'C', 'GSW', 'LAL', 'g1', date(2025, 10, 21),
                '2025-26', 'AWAY', True,
                pts=25.0, reb=4.0, ast=6.0, fg3m=2.0, fga=22.0, fta=5.0,
                tov=2.0, minutes=38.0, team_score=110.0, opp_score=120.0),
            log('1', 'A', 'LAL', 'BOS', 'g0', date(2025, 1, 10),
                '2024-25', 'AWAY', False,
                pts=18.0, reb=5.0, ast=4.0, fg3m=0.0, fga=15.0, fta=4.0,
                tov=1.0, minutes=28.0, team_score=98.0, opp_score=104.0),
        ])
        db.session.add(HistoricalGameOdds(
            game_date=date(2025, 10, 21), home_abbr='LAL', away_abbr='GSW',
            spread=6.5, favored='home', total=220.0))
        db.session.commit()

    def test_load_frame_flattens_stats_and_computes_pra(self):
        from app.services.scenario_dimensions import load_frame
        with self.app.app_context():
            self._seed()
            df = load_frame()
            self.assertEqual(len(df), 3)     # one row per player-game
            for col in ('player_id', 'player_name', 'game_id', 'game_date',
                        'season', 'team_abbr', 'opp_abbr', 'home_away',
                        'starter', 'pts', 'reb', 'ast', 'fg3m', 'fga',
                        'fta', 'tov', 'minutes', 'team_score', 'opp_score',
                        'pra'):
                self.assertIn(col, df.columns)
            row = df[(df.player_id == '1') & (df.game_id == 'g1')].iloc[0]
            self.assertEqual(row['player_name'], 'A')
            self.assertEqual(row['season'], '2025-26')
            self.assertEqual(row['game_date'], date(2025, 10, 21))
            self.assertEqual(row['pts'], 30.0)
            self.assertEqual(row['team_score'], 120.0)
            self.assertEqual(row['opp_score'], 110.0)
            self.assertTrue(bool(row['starter']))
            self.assertEqual(row['pra'], 47.0)   # 30+8+9, computed not stored

    def test_load_frame_seasons_filter(self):
        from app.services.scenario_dimensions import load_frame
        with self.app.app_context():
            self._seed()
            df = load_frame(seasons=['2025-26'])
            self.assertEqual(len(df), 2)
            self.assertEqual(set(df['game_id']), {'g1'})
            self.assertEqual(set(df['season']), {'2025-26'})

    def test_load_odds_frame(self):
        from app.services.scenario_dimensions import load_odds_frame
        with self.app.app_context():
            self._seed()
            odf = load_odds_frame()
            self.assertEqual(len(odf), 1)
            self.assertEqual(
                list(odf.columns),
                ['game_date', 'home_abbr', 'away_abbr', 'spread',
                 'favored', 'total'])
            row = odf.iloc[0]
            self.assertEqual(row['game_date'], date(2025, 10, 21))
            self.assertEqual(row['home_abbr'], 'LAL')
            self.assertEqual(row['away_abbr'], 'GSW')
            self.assertEqual(row['spread'], 6.5)
            self.assertEqual(row['favored'], 'home')
            self.assertEqual(row['total'], 220.0)


class TestSharedBucketHelpers(BaseTestCase):

    def test_rest_bucket_label_boundaries(self):
        from app.services.scenario_dimensions import rest_bucket_label
        self.assertEqual(rest_bucket_label(0), '0')
        self.assertEqual(rest_bucket_label(1), '1')
        self.assertEqual(rest_bucket_label(2), '2')
        self.assertEqual(rest_bucket_label(3), '3+')
        self.assertEqual(rest_bucket_label(99), '3+')   # first-game convention

    def test_season_segment_label_month_edges(self):
        from datetime import date
        from app.services.scenario_dimensions import season_segment_label
        self.assertEqual(season_segment_label(date(2025, 10, 25)), 'early')
        self.assertEqual(season_segment_label(date(2025, 12, 31)), 'early')
        self.assertEqual(season_segment_label(date(2026, 1, 1)), 'mid')
        self.assertEqual(season_segment_label(date(2026, 2, 28)), 'mid')
        self.assertEqual(season_segment_label(date(2026, 3, 1)), 'late')
        self.assertEqual(season_segment_label(date(2026, 4, 12)), 'late')

    def test_fav_dog_label_matches_historical_rules(self):
        from app.services.scenario_dimensions import fav_dog_label
        self.assertEqual(fav_dog_label(9.5, True), 'fav_big')
        self.assertEqual(fav_dog_label(7.0, True), 'fav')       # big is strictly > 7
        self.assertEqual(fav_dog_label(3.0, False), 'dog')
        self.assertEqual(fav_dog_label(10.0, False), 'dog_big')
        self.assertEqual(fav_dog_label(0.0, False), 'fav')      # pick'em convention
