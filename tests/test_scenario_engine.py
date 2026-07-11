"""Engine math + end-to-end materialization tests."""

import pandas as pd

from tests.helpers import BaseTestCase
from tests.test_scenario_dimensions import _mini_frame


class TestPriorStrength(BaseTestCase):

    def test_k_fit_method_of_moments_and_clamps(self):
        from app.services.scenario_engine import fit_prior_strength
        # two players, wildly different means, low within-noise -> small k
        df = pd.DataFrame({
            'player_id': ['1'] * 4 + ['2'] * 4,
            'pts': [30, 31, 29, 30, 10, 11, 9, 10],
        })
        k_small = fit_prior_strength(df, 'pts')
        self.assertEqual(k_small, 2.0)              # clamped at floor
        # identical means, pure noise -> k at cap
        df2 = pd.DataFrame({
            'player_id': ['1'] * 4 + ['2'] * 4,
            'pts': [10, 30, 20, 40, 25, 5, 35, 15],
        })
        self.assertEqual(fit_prior_strength(df2, 'pts'), 25.0)

    def test_shrunk_mean_formula(self):
        from app.services.scenario_engine import shrink
        # (n*raw + k*baseline) / (n+k): (4*20 + 6*10) / 10 = 14
        self.assertAlmostEqual(shrink(raw=20.0, n=4, baseline=10.0, k=6.0),
                               14.0)


class TestRefreshSplits(BaseTestCase):

    def _seed_store(self):
        """Persist the mini frame as HistoricalGameLog rows (>=15-game gate
        disabled via min_games param)."""
        from app import db
        from app.models import HistoricalGameLog
        for rec in _mini_frame().to_dict('records'):
            stats = {k: float(rec[k]) for k in
                     ('pts', 'reb', 'ast', 'fg3m', 'fga', 'fta', 'tov',
                      'minutes', 'team_score', 'opp_score')}
            stats['usage_pct'] = 0.2
            db.session.add(HistoricalGameLog(
                sport='nba', player_id=rec['player_id'],
                player_name=rec['player_name'], team_abbr=rec['team_abbr'],
                opp_abbr=rec['opp_abbr'], game_id=rec['game_id'],
                game_date=rec['game_date'], season=rec['season'],
                home_away=rec['home_away'], win_loss='W',
                starter=rec['starter'], stats=stats))
        db.session.commit()

    def test_end_to_end_materialization(self):
        from app.models import JobLog, ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            result = refresh_splits(sport='nba', min_games=1)
            self.assertGreater(result['rows'], 0)
            self.assertEqual(result['players'], 3)
            # player 1 HOME pts: games g1,g3,g4 -> raw mean (30+28+35)/3=31
            row = ScenarioSplit.query.filter_by(
                player_id='1', stat='pts', dim1='home_away', bucket1='HOME',
                dim2=None, season_scope='all').one()
            self.assertEqual(row.n, 3)
            self.assertAlmostEqual(row.raw_mean, 31.0)
            # shrunk sits strictly between raw and baseline
            self.assertTrue(min(row.raw_mean, row.baseline_mean)
                            <= row.shrunk_mean
                            <= max(row.raw_mean, row.baseline_mean))
            job = JobLog.query.filter_by(
                job_name='refresh-scenario-splits').one()
            self.assertEqual(job.status, 'success')

    def test_refresh_replaces_not_duplicates(self):
        from app.models import ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            refresh_splits(sport='nba', min_games=1)
            first = ScenarioSplit.query.count()
            refresh_splits(sport='nba', min_games=1, force=True)
            self.assertEqual(ScenarioSplit.query.count(), first)

    def test_no_new_data_guard(self):
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            refresh_splits(sport='nba', min_games=1)
            result = refresh_splits(sport='nba', min_games=1)   # no force
            self.assertEqual(result['skipped_reason'], 'no_new_data')

    def test_n_below_3_not_stored(self):
        from app.models import ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            self._seed_store()
            refresh_splits(sport='nba', min_games=1)
            self.assertEqual(
                ScenarioSplit.query.filter(ScenarioSplit.n < 3).count(), 0)


class TestAgreementScore(BaseTestCase):

    def test_signed_weighted_agreement(self):
        from app import db
        from app.models import ScenarioSplit
        from app.services.scenario_engine import agreement_score
        with self.app.app_context():
            def split(dim1, b1, shrunk, n, dim2=None, b2=None):
                db.session.add(ScenarioSplit(
                    sport='nba', player_id='1', player_name='A', stat='pts',
                    dim1=dim1, bucket1=b1, dim2=dim2, bucket2=b2,
                    season_scope='all', n=n, raw_mean=shrunk,
                    shrunk_mean=shrunk, baseline_mean=25.0))
            split('home_away', 'HOME', 30.0, 10)     # over 25.5, w10
            split('rest_bucket', '0', 28.0, 5)       # over, w5
            split('home_away', 'HOME', 22.0, 5,
                  dim2='rest_bucket', b2='0')        # under, w5
            db.session.commit()
            score, n_splits = agreement_score(
                '1', 'pts', 25.5,
                {'home_away': 'HOME', 'rest_bucket': '0'})
            self.assertEqual(n_splits, 3)
            # (10 + 5 - 5) / 20 = +0.5
            self.assertAlmostEqual(score, 0.5)

    def test_no_matching_splits(self):
        from app.services.scenario_engine import agreement_score
        with self.app.app_context():
            score, n = agreement_score('9', 'pts', 20.0,
                                       {'home_away': 'AWAY'})
            self.assertEqual((score, n), (0.0, 0))
