"""Bucket-function tests over a hand-built mini store."""

from datetime import date

import pandas as pd

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
