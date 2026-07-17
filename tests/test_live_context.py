"""Tests for the live scenario-context builder."""

import json
from datetime import date, datetime, timedelta, timezone

from app import db
from app.models import HistoricalGameLog, ScenarioContextPack
from tests.helpers import BaseTestCase


def _seed_pack(computed_at=None):
    db.session.add(ScenarioContextPack(
        sport='nba',
        payload=json.dumps({
            'season': '2025-26',
            'total_bins': [200.0, 221.0, 229.0, 260.0],
            'pace_bins': [180.0, 196.0, 204.0, 230.0],
            'team_game_poss': {'DEN': 198.0, 'LAL': 207.0},
            'team_def_tier': {'DEN': 'top10', 'LAL': 'bottom10'},
        }),
        computed_at=computed_at or datetime.now(timezone.utc)))
    db.session.commit()


def _seed_history(player_id='558', n=6, starter=True, end=date(2026, 1, 10)):
    for i in range(n):
        db.session.add(HistoricalGameLog(
            sport='nba', player_id=player_id, player_name='Test Player',
            team_abbr='DEN', opp_abbr='LAL', game_id=f'g{i}',
            game_date=end - timedelta(days=(n - 1 - i) * 2), season='2025-26',
            home_away='home', win_loss='W', starter=starter,
            stats={'pts': 20.0}, fetched_at=datetime.now(timezone.utc)))
    db.session.commit()


class TestBuildLiveContext(BaseTestCase):

    def _ctx(self, **kw):
        from app.services.live_context import build_live_context
        args = dict(team_abbr='DEN', opponent_abbr='LAL', is_home=True,
                    game_date=date(2026, 1, 12), total=228.5,
                    spread=8.5, favored_side='home')
        args.update(kw)
        return build_live_context('558', **args)

    def test_full_context_with_pack_and_history(self):
        with self.app.app_context():
            _seed_pack()
            _seed_history()
            ctx, fresh = self._ctx()
        self.assertTrue(fresh)
        self.assertEqual(ctx['home_away'], 'home')
        self.assertEqual(ctx['season_segment'], 'mid')
        self.assertEqual(ctx['rest_bucket'], '1')       # last game 01-10, game 01-12
        self.assertEqual(ctx['role'], 'starter')
        self.assertEqual(ctx['opp_def_tier'], 'bottom10')
        self.assertEqual(ctx['pace_tier'], 'mid')       # (198+207)/2=202.5 in (196,204]
        self.assertEqual(ctx['fav_dog'], 'fav_big')
        self.assertEqual(ctx['total_bucket'], 'mid')    # 228.5 in (221,229]
        self.assertNotIn('game_script', ctx)
        self.assertNotIn('teammate_context', ctx)

    def test_missing_pack_degrades_to_fixed_dims_only(self):
        with self.app.app_context():
            _seed_history()
            ctx, fresh = self._ctx()
        self.assertFalse(fresh)
        for dim in ('opp_def_tier', 'pace_tier', 'total_bucket'):
            self.assertNotIn(dim, ctx)
        self.assertIn('home_away', ctx)
        self.assertIn('fav_dog', ctx)                   # spread came from the slate

    def test_stale_pack_still_builds_but_reports_not_fresh(self):
        with self.app.app_context():
            _seed_pack(computed_at=datetime.now(timezone.utc) - timedelta(days=30))
            _seed_history()
            ctx, fresh = self._ctx()
        self.assertFalse(fresh)
        self.assertIn('opp_def_tier', ctx)

    def test_no_history_first_game_conventions(self):
        with self.app.app_context():
            _seed_pack()
            ctx, _ = self._ctx()
        self.assertEqual(ctx['rest_bucket'], '3+')      # no prior game -> 99 -> 3+
        self.assertNotIn('role', ctx)                   # no starter evidence -> absent

    def test_no_spread_omits_fav_dog(self):
        with self.app.app_context():
            _seed_pack()
            _seed_history()
            ctx, _ = self._ctx(spread=None, favored_side=None)
        self.assertNotIn('fav_dog', ctx)
