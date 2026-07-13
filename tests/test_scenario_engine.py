"""Engine math + end-to-end materialization tests."""

import random
from datetime import date, timedelta
from itertools import combinations
from unittest.mock import patch

import pandas as pd

from tests.helpers import BaseTestCase
from tests.test_scenario_dimensions import _mini_frame


def _rich_frame():
    """Larger synthetic store: 8 players / 4 teams / 2 seasons, ~20 games
    per player per season. Deterministic (fixed seed). Big/varied enough
    that some pairwise (dim1, dim2) buckets reach n>=3 and some fall
    below MIN_N -- unlike ``_mini_frame`` which only exercises single-dim
    splits at n=3."""
    rng = random.Random(20260712)
    teams = ['LAL', 'BOS', 'GSW', 'MIA']
    players = {}
    pid = 1
    for team in teams:
        for slot in range(2):
            players[(team, slot)] = (str(pid), f'P{pid}')
            pid += 1

    rows = []
    game_counter = 1
    seasons = [('2024-25', date(2024, 10, 22)),
               ('2025-26', date(2025, 10, 21))]
    for season, start in seasons:
        cur_date = start
        for _ in range(40):
            home, away = rng.sample(teams, 2)
            cur_date = cur_date + timedelta(days=rng.choice([1, 1, 2, 3]))
            game_id = f'g{game_counter}'
            game_counter += 1
            home_score = rng.randint(95, 130)
            away_score = rng.randint(95, 130)
            for team, opp, ha, team_score, opp_score in (
                    (home, away, 'HOME', home_score, away_score),
                    (away, home, 'AWAY', away_score, home_score)):
                for slot in range(2):
                    p_id, p_name = players[(team, slot)]
                    starter = slot == 0
                    base = 22 if starter else 10
                    pts = max(0, base + rng.randint(-8, 12))
                    reb = max(0, rng.randint(2, 10))
                    ast = max(0, rng.randint(1, 9))
                    fg3m = max(0, rng.randint(0, 5))
                    fga = max(pts // 2, rng.randint(5, 20))
                    fta = rng.randint(0, 8)
                    tov = rng.randint(0, 5)
                    minutes = (rng.randint(15, 40) if starter
                              else rng.randint(8, 25))
                    rows.append(dict(
                        player_id=p_id, player_name=p_name, team_abbr=team,
                        opp_abbr=opp, game_id=game_id, game_date=cur_date,
                        season=season, home_away=ha, starter=starter,
                        pts=float(pts), reb=float(reb), ast=float(ast),
                        fg3m=float(fg3m), fga=float(fga), fta=float(fta),
                        tov=float(tov), minutes=float(minutes),
                        team_score=float(team_score),
                        opp_score=float(opp_score)))
    return pd.DataFrame(rows)


def _oracle_splits(df, min_games=1, sport='nba'):
    """Independent reference implementation of refresh_splits' split set.

    Shares the real build_context/fit_prior_strength/shrink/DIMENSIONS/
    SPLIT_STATS with production, but iterates player-by-player then
    combo-by-combo (nested Python loops), never the vectorized
    groupby-across-all-players-at-once path the engine uses. If the
    vectorized refactor and this naive path disagree, the refactor is
    behaviorally wrong.
    """
    from app.services.scenario_dimensions import (
        DIMENSIONS, SPLIT_STATS, build_context, load_odds_frame,
    )
    from app.services.scenario_engine import MIN_N, fit_prior_strength, shrink

    ctx = build_context(df, odds_df=load_odds_frame())
    seasons = sorted(ctx['season'].unique())[-2:]
    scope_all = ctx[ctx['season'].isin(seasons)]
    counts = scope_all.groupby('player_id')['game_id'].nunique()
    eligible = set(counts[counts >= min_games].index)
    ks = {stat: fit_prior_strength(scope_all, stat) for stat in SPLIT_STATS}
    current = seasons[-1]

    results = set()
    for scope_name, frame in (('all', scope_all),
                              (current,
                               scope_all[scope_all['season'] == current])):
        frame = frame[frame['player_id'].isin(eligible)]
        if frame.empty:
            continue
        baselines = frame.groupby('player_id')[list(SPLIT_STATS)].mean()
        dims = list(DIMENSIONS)
        combos = [(d, None) for d in dims] + list(combinations(dims, 2))
        for dim1, dim2 in combos:
            c1 = f'ctx_{dim1}'
            c2 = f'ctx_{dim2}' if dim2 else None
            for pid, pframe in frame.groupby('player_id'):
                subset_cols = [c1] + ([c2] if c2 else [])
                sub = pframe.dropna(subset=subset_cols)
                if sub.empty:
                    continue
                groups = sub.groupby(subset_cols, observed=True)
                for key, g in groups:
                    key_t = key if isinstance(key, tuple) else (key,)
                    b1 = str(key_t[0])
                    b2 = str(key_t[1]) if dim2 else None
                    for stat in SPLIT_STATS:
                        n = int(g[stat].count())
                        if n < MIN_N:
                            continue
                        raw = float(g[stat].mean())
                        base = float(baselines.loc[pid, stat])
                        shrunk = shrink(raw, n, base, ks[stat])
                        results.add((
                            pid, stat, dim1, b1, dim2, b2, scope_name, n,
                            round(raw, 6), round(shrunk, 6), round(base, 6)))
    return results


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

    def _seed_rich_store(self):
        from app import db
        from app.models import HistoricalGameLog
        for rec in _rich_frame().to_dict('records'):
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

    def test_golden_master_full_row_set_matches_naive_oracle(self):
        """Proves the vectorized combo loop is output-identical to an
        independently-implemented naive reference, not just "produces
        some rows". This is the real safety net for the perf refactor."""
        from app.models import ScenarioSplit
        from app.services.scenario_engine import refresh_splits
        with self.app.app_context():
            df = _rich_frame()
            self._seed_rich_store()
            oracle = _oracle_splits(df, min_games=1, sport='nba')
            self.assertGreater(len(oracle), 0)
            # sanity: the fixture actually exercises the filter both ways
            oracle_pairwise_n = [row for row in oracle if row[4] is not None]
            self.assertGreater(len(oracle_pairwise_n), 0,
                               "fixture produced no eligible pairwise splits"
                               " -- richer fixture needed")

            result = refresh_splits(sport='nba', min_games=1, force=True)
            self.assertGreater(result['rows'], 0)

            actual = set()
            for s in ScenarioSplit.query.filter_by(sport='nba').all():
                actual.add((
                    s.player_id, s.stat, s.dim1, s.bucket1, s.dim2,
                    s.bucket2, s.season_scope, s.n,
                    round(s.raw_mean, 6), round(s.shrunk_mean, 6),
                    round(s.baseline_mean, 6)))

            self.assertEqual(actual, oracle)


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


class TestWiring(BaseTestCase):

    @patch('app.services.scenario_engine.refresh_splits',
           return_value={'players': 2, 'rows': 40, 'skipped_reason': None})
    def test_refresh_cli(self, mock_refresh):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['refresh-splits', '--force'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('rows=40', result.output)
        mock_refresh.assert_called_once_with(sport='nba', force=True)

    def test_show_splits_cli(self):
        from app import db
        from app.models import ScenarioSplit
        with self.app.app_context():
            db.session.add(ScenarioSplit(
                sport='nba', player_id='1', player_name='LeBron James',
                stat='pts', dim1='home_away', bucket1='HOME', dim2=None,
                bucket2=None, season_scope='all', n=41, raw_mean=27.1,
                shrunk_mean=26.8, baseline_mean=26.2))
            db.session.commit()
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['show-splits', '--player',
                                     'LeBron James', '--stat', 'pts'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('home_away=HOME', result.output)
        self.assertIn('26.8', result.output)

    def test_scheduler_job_registered(self):
        """Behavioral check: init_scheduler registers the Plan B job with the
        right id, cron kwargs (05:10 ET), and replace_existing=True.

        Mirrors the FakeScheduler pattern in
        tests/test_services.py::TestScheduler.test_init_scheduler_adds_jobs
        (defined inline there, so not importable).
        """
        from app.services import scheduler as scheduler_module

        class FakeScheduler:
            def __init__(self):
                self.running = False
                self.jobs = {}
                self.started = False

            def add_job(self, func, trigger, id=None, replace_existing=None):
                self.jobs[id] = (trigger, replace_existing)

            def start(self):
                self.started = True

            def get_jobs(self):
                return list(self.jobs)

        fake = FakeScheduler()
        with patch.object(scheduler_module, 'scheduler', fake):
            with patch.object(scheduler_module, 'CronTrigger',
                              side_effect=lambda **kw: kw):
                with patch.object(scheduler_module, '_acquire_scheduler_lock',
                                  return_value=True):
                    scheduler_module.init_scheduler(self.app)
        self.assertTrue(fake.started)
        self.assertIn('refresh_scenario_splits', fake.jobs)
        trigger, replace_existing = fake.jobs['refresh_scenario_splits']
        self.assertEqual(trigger, {'hour': 5, 'minute': 10,
                                   'timezone': scheduler_module.APP_TIMEZONE})
        self.assertTrue(replace_existing)
