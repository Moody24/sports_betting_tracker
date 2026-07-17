"""Keystone parity test: live bucketing must equal historical bucketing.

Runs the REAL historical build_context over a seeded store, builds the pack
from the same frame, then replays build_live_context as-of the final game
and asserts every reconstructable dimension emits the SAME bucket label the
historical builder assigned to that row.

Strict-equality dims: home_away, season_segment, rest_bucket, total_bucket,
fav_dog, opp_def_tier (the fixture's sampled game is the frame's LAST game,
where the pack's as-of-latest def ranking coincides with build_context's
as-of-date ranking). pace_tier is NOT strict: training buckets the game's
REALIZED possessions while live estimates from team averages — assert only
a valid label. role is NOT strict either (training uses the game's actual
starter flag; live predicts from last-5) — the fixture makes the player an
every-game starter so both agree.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from app import db
from app.models import (
    HistoricalGameLog,
    HistoricalGameOdds,
    ScenarioContextPack,
)
from tests.helpers import BaseTestCase

TEAMS = ('DEN', 'LAL', 'BOS', 'NYK')


def _seed_league(n_rounds=6, start=date(2026, 1, 2)):
    """Two fixed matchups per round; player DEN-0 starts every DEN game."""
    gid = 0
    for rnd in range(n_rounds):
        d = start + timedelta(days=rnd * 2)
        for a, b in ((TEAMS[0], TEAMS[1]), (TEAMS[2], TEAMS[3])):
            gid += 1
            game_id = f'pg{gid}'
            for team, opp, ha in ((a, b, 'home'), (b, a, 'away')):
                for slot in range(3):        # 3 players/team so poss vary
                    db.session.add(HistoricalGameLog(
                        sport='nba', player_id=f'{team}-{slot}',
                        player_name=f'{team} Player{slot}', team_abbr=team,
                        opp_abbr=opp, game_id=game_id, game_date=d,
                        season='2025-26', home_away=ha, win_loss='W',
                        starter=True,
                        stats={'pts': 20.0 + slot + rnd, 'reb': 5.0,
                               'ast': 4.0, 'stl': 1.0, 'blk': 0.5,
                               'tov': 2.0 + slot, 'fgm': 8.0,
                               'fga': 15.0 + slot + rnd, 'fg3m': 2.0,
                               'fg3a': 6.0, 'ftm': 3.0, 'fta': 4.0 + rnd,
                               'minutes': 30.0, 'plus_minus': 3.0,
                               'usage_pct': 0.2, 'team_score': 110.0 + rnd,
                               'opp_score': 104.0 + slot},
                        fetched_at=datetime.now(timezone.utc)))
            db.session.add(HistoricalGameOdds(
                game_date=d, home_abbr=a, away_abbr=b,
                spread=4.5 + rnd, favored='home', total=220.0 + rnd * 3,
                moneyline_home=-180, moneyline_away=150,
                is_playoff=False, source='test', espn_game_id=game_id))
    db.session.commit()


class TestLiveHistoricalParity(BaseTestCase):

    def test_live_buckets_equal_historical_buckets_for_final_game(self):
        from app.services.live_context import build_live_context
        from app.services.scenario_dimensions import (
            build_context,
            build_context_pack,
            load_frame,
            load_odds_frame,
        )
        with self.app.app_context():
            _seed_league()
            frame, odds = load_frame(), load_odds_frame()
            ctx_df = build_context(frame, odds_df=odds)

            row = (ctx_df[ctx_df['player_id'] == 'DEN-0']
                   .sort_values('game_date').iloc[-1])
            game_odds = HistoricalGameOdds.query.filter_by(
                espn_game_id=row['game_id']).first()

            db.session.add(ScenarioContextPack(
                sport='nba',
                payload=json.dumps(build_context_pack(frame, odds)),
                computed_at=datetime.now(timezone.utc)))
            db.session.commit()

            is_home = row['home_away'] == 'home'
            live, fresh = build_live_context(
                'DEN-0', team_abbr='DEN', opponent_abbr='LAL',
                is_home=is_home, game_date=row['game_date'].date(),
                total=float(game_odds.total), spread=float(game_odds.spread),
                favored_side=str(game_odds.favored))

        self.assertTrue(fresh)
        strict = {'home_away': 'ctx_home_away',
                  'season_segment': 'ctx_season_segment',
                  'rest_bucket': 'ctx_rest_bucket',
                  'total_bucket': 'ctx_total_bucket',
                  'fav_dog': 'ctx_fav_dog',
                  'opp_def_tier': 'ctx_opp_def_tier'}
        for live_dim, hist_col in strict.items():
            hist_val = row[hist_col]
            if pd.isna(hist_val):
                continue          # dim the historical builder couldn't populate
            self.assertIn(live_dim, live,
                          f'live context missing {live_dim}')
            self.assertEqual(live[live_dim], str(hist_val),
                             f'parity broken for {live_dim}')
        if 'pace_tier' in live:
            self.assertIn(live['pace_tier'], ('slow', 'mid', 'fast'))
        self.assertEqual(live.get('role'), 'starter')   # every-game starter fixture
        self.assertNotIn('game_script', live)
