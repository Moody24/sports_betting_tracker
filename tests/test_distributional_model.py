"""Tests for the Plan C distributional multi-quantile training pipeline."""

from datetime import date, timedelta
from unittest.mock import patch

from app import db
from app.models import ModelMetadata, PlayerGameLog
from tests.helpers import BaseTestCase


def _seed_dist_logs(player_id='701', count=40, base_pts=20.0, base_reb=6.0,
                    base_ast=5.0, seed_offset=0):
    """Insert ``count`` game logs for one player with enough spread across
    pts/reb/ast that quantile training has real signal. Returns the logs."""
    logs = []
    for i in range(count):
        pts = max(base_pts + ((i + seed_offset) % 9) - 4, 0.0)
        reb = max(base_reb + ((i + seed_offset) % 5) - 2, 0.0)
        ast = max(base_ast + ((i + seed_offset) % 4) - 1, 0.0)
        log = PlayerGameLog(
            player_id=player_id,
            player_name=f'Dist Player {player_id}',
            team_abbr='TST',
            game_date=date(2024, 1, 1) + timedelta(days=i),
            matchup='TST vs. OPP' if i % 2 == 0 else 'TST @ OPP',
            minutes=32.0,
            pts=pts, reb=reb, ast=ast,
            fg3m=2.0, stl=1.0, blk=0.5, tov=2.0,
            fgm=8.0, fga=17.0, ftm=4.0, fta=5.0, fg3a=6.0,
            home_away='home' if i % 2 == 0 else 'away',
        )
        db.session.add(log)
        logs.append(log)
    db.session.commit()
    return logs


class TestDistStatConstants(BaseTestCase):

    def test_dist_stat_types_and_key_map(self):
        from app.services.distributional_model import DIST_STAT_TYPES, DIST_STAT_KEY_MAP, POISSON_DIST_STAT_TYPES
        self.assertEqual(
            DIST_STAT_TYPES,
            ['player_points', 'player_rebounds', 'player_assists', 'player_points_rebounds_assists'],
        )
        self.assertEqual(DIST_STAT_KEY_MAP['player_points_rebounds_assists'], 'pra')
        self.assertEqual(POISSON_DIST_STAT_TYPES, ['player_threes', 'player_steals', 'player_blocks'])


class TestPRALogProxy(BaseTestCase):

    def test_pra_proxy_sums_and_delegates(self):
        from app.services.distributional_model import _PRALogProxy
        with self.app.app_context():
            [log] = _seed_dist_logs(player_id='555', count=1)
            proxy = _PRALogProxy(log)
            self.assertEqual(proxy.pra, log.pts + log.reb + log.ast)
            self.assertEqual(proxy.team_abbr, log.team_abbr)
            self.assertEqual(proxy.game_date, log.game_date)
            self.assertEqual(proxy.fga, log.fga)

    def test_wrap_pra_logs_returns_proxies(self):
        from app.services.distributional_model import wrap_pra_logs, _PRALogProxy
        with self.app.app_context():
            logs = _seed_dist_logs(player_id='556', count=3)
            wrapped = wrap_pra_logs(logs)
            self.assertEqual(len(wrapped), 3)
            self.assertIsInstance(wrapped[0], _PRALogProxy)


class TestDateCutoffSplit(BaseTestCase):

    def test_splits_by_date_when_enough_unique_dates(self):
        from app.services.distributional_model import _date_cutoff_split
        rows = [(date(2024, 1, 1) + timedelta(days=i), 'p1', {}, float(i)) for i in range(10)]
        train_idx, val_idx, method, cutoff = _date_cutoff_split(rows, frac=0.8)
        self.assertEqual(method, 'date_cutoff')
        self.assertIsNotNone(cutoff)
        self.assertTrue(train_idx)
        self.assertTrue(val_idx)
        self.assertEqual(set(train_idx) | set(val_idx), set(range(10)))

    def test_falls_back_to_index_split_with_one_date(self):
        from app.services.distributional_model import _date_cutoff_split
        rows = [(date(2024, 1, 1), 'p1', {}, float(i)) for i in range(10)]
        train_idx, val_idx, method, cutoff = _date_cutoff_split(rows, frac=0.8)
        self.assertEqual(method, 'index_fallback')
        self.assertTrue(train_idx)
        self.assertTrue(val_idx)


class TestBuildDistTrainingRows(BaseTestCase):

    def test_pra_target_equals_realized_sum(self):
        from app.services import distributional_model as dm
        with self.app.app_context():
            _seed_dist_logs(player_id='556', count=15)
            with patch.object(dm, 'MIN_TRAIN_SAMPLES', 1):
                rows = dm._build_dist_training_rows('player_points_rebounds_assists')
            self.assertTrue(rows)
            game_date, pid, _features, target = rows[0]
            log = PlayerGameLog.query.filter_by(player_id='556', game_date=game_date).first()
            self.assertAlmostEqual(target, log.pts + log.reb + log.ast)

    def test_points_target_equals_pts_column(self):
        from app.services import distributional_model as dm
        with self.app.app_context():
            _seed_dist_logs(player_id='557', count=15)
            with patch.object(dm, 'MIN_TRAIN_SAMPLES', 1):
                rows = dm._build_dist_training_rows('player_points')
            self.assertTrue(rows)
            game_date, pid, features, target = rows[0]
            log = PlayerGameLog.query.filter_by(player_id='557', game_date=game_date).first()
            self.assertAlmostEqual(target, log.pts)
            self.assertIn('avg_stat_last_5', features)

    def test_unsupported_stat_type_returns_empty(self):
        from app.services.distributional_model import _build_dist_training_rows
        with self.app.app_context():
            self.assertEqual(_build_dist_training_rows('player_threes'), [])


class TestTrainDistributionalModel(BaseTestCase):

    def test_insufficient_data_returns_error(self):
        from app.services.distributional_model import train_distributional_model
        with self.app.app_context():
            result = train_distributional_model('player_points')
        self.assertIn('error', result)

    def test_unsupported_stat_type_returns_error(self):
        from app.services.distributional_model import train_distributional_model
        with self.app.app_context():
            result = train_distributional_model('player_threes')
        self.assertIn('error', result)

    def test_trains_and_persists_quantile_metadata(self):
        from app.services import distributional_model as dm
        import json as _json
        with self.app.app_context():
            for pid in ('601', '602', '603'):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(dm, 'MIN_TRAIN_SAMPLES', 50):
                result = dm.train_distributional_model('player_points')

            self.assertNotIn('error', result)
            self.assertEqual(result['stat_type'], 'player_points')
            self.assertGreater(result['train_samples'], 0)
            self.assertGreater(result['val_samples'], 0)

            meta = ModelMetadata.query.filter_by(model_name='dist_player_points', is_active=True).first()
            self.assertIsNotNone(meta)
            self.assertEqual(meta.model_type, 'xgboost_quantile_regressor')
            md = _json.loads(meta.metadata_json)
            self.assertEqual(md['quantile_alphas'], dm.QUANTILE_ALPHAS)
            self.assertEqual(md['calibrator_model_name'], 'dist_calibrator_player_points')

    def test_predictions_are_rectifiable_after_save_load(self):
        from app.services import distributional_model as dm
        from app.services.model_storage import materialize_model_artifact
        from app.services.distribution import rectify_quantiles
        from xgboost import XGBRegressor
        import json as _json
        import numpy as np

        with self.app.app_context():
            for pid in ('611', '612', '613'):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(dm, 'MIN_TRAIN_SAMPLES', 50):
                dm.train_distributional_model('player_rebounds')
            meta = ModelMetadata.query.filter_by(model_name='dist_player_rebounds', is_active=True).first()
            local_path = materialize_model_artifact(meta.file_path)
            feature_names = _json.loads(meta.metadata_json)['feature_names']

        model = XGBRegressor()
        model.load_model(local_path)
        X = np.zeros((1, len(feature_names)))
        raw = model.predict(X)[0].tolist()
        self.assertEqual(len(raw), len(dm.QUANTILE_ALPHAS))
        rectified = rectify_quantiles(raw)
        self.assertEqual(rectified, sorted(rectified))


class TestTrainDistributionalModelWithCalibrator(BaseTestCase):

    def test_train_persists_calibrator_metadata(self):
        from app.services import distributional_model as dm
        with self.app.app_context():
            for pid in ('621', '622', '623'):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(dm, 'MIN_TRAIN_SAMPLES', 50):
                result = dm.train_distributional_model('player_assists')

            self.assertIn('calibrator_fitted', result)
            self.assertTrue(result['calibrator_fitted'])
            self.assertGreater(result['calibration_pairs'], 0)

            calib_meta = ModelMetadata.query.filter_by(
                model_name='dist_calibrator_player_assists', is_active=True,
            ).first()
            self.assertIsNotNone(calib_meta)
            self.assertEqual(calib_meta.model_type, 'isotonic_calibrator')


class TestPoissonOofCalibration(BaseTestCase):

    def test_collect_poisson_oof_rows_from_trained_point_model(self):
        from app.services import ml_model
        from app.services.distributional_model import _collect_poisson_oof_rows

        with self.app.app_context():
            for pid in ('631', '632', '633'):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(ml_model, 'MIN_TRAIN_SAMPLES', 50):
                ml_model.train_model('player_steals')
            oof_rows = _collect_poisson_oof_rows('player_steals')

        self.assertTrue(oof_rows)
        for lam, realized in oof_rows:
            self.assertGreater(lam, 0.0)
            self.assertGreaterEqual(realized, 0.0)

    def test_collect_poisson_oof_rows_no_active_model_returns_empty(self):
        from app.services.distributional_model import _collect_poisson_oof_rows
        with self.app.app_context():
            self.assertEqual(_collect_poisson_oof_rows('player_steals'), [])

    def test_train_calibrator_for_poisson_stat_persists_metadata(self):
        from app.services import ml_model
        from app.services.distributional_model import train_distributional_calibrator_for_poisson_stat

        with self.app.app_context():
            for pid in ('641', '642', '643'):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(ml_model, 'MIN_TRAIN_SAMPLES', 50):
                ml_model.train_model('player_blocks')
            result = train_distributional_calibrator_for_poisson_stat('player_blocks')

            self.assertNotIn('error', result)
            self.assertGreater(result['calibration_pairs'], 0)
            meta = ModelMetadata.query.filter_by(
                model_name='dist_calibrator_player_blocks', is_active=True,
            ).first()
            self.assertIsNotNone(meta)
            self.assertEqual(meta.model_type, 'isotonic_calibrator')

    def test_train_calibrator_for_poisson_stat_unsupported_type(self):
        from app.services.distributional_model import train_distributional_calibrator_for_poisson_stat
        with self.app.app_context():
            result = train_distributional_calibrator_for_poisson_stat('player_points')
        self.assertIn('error', result)


class TestRetrainAllDistributionalModels(BaseTestCase):

    def test_calls_quantile_and_poisson_training_for_every_stat_type(self):
        from app.services import distributional_model as dm

        with patch.object(dm, 'train_distributional_model', return_value={'ok': True}) as mock_q, \
             patch.object(
                 dm, 'train_distributional_calibrator_for_poisson_stat', return_value={'ok': True},
             ) as mock_p:
            with self.app.app_context():
                results = dm.retrain_all_distributional_models()

        self.assertEqual(mock_q.call_count, len(dm.DIST_STAT_TYPES))
        self.assertEqual(mock_p.call_count, len(dm.POISSON_DIST_STAT_TYPES))
        for stat_type in dm.DIST_STAT_TYPES + dm.POISSON_DIST_STAT_TYPES:
            self.assertIn(stat_type, results)


class TestBacktestVerdict(BaseTestCase):

    def test_promotes_when_under_gate_and_better_than_incumbent(self):
        from app.services.distributional_model import backtest_verdict
        self.assertEqual(backtest_verdict(dist_ece=0.02, gauss_ece=0.10), 'PROMOTE')

    def test_holds_when_over_gate_even_if_better_than_incumbent(self):
        from app.services.distributional_model import backtest_verdict
        self.assertEqual(backtest_verdict(dist_ece=0.05, gauss_ece=0.10), 'HOLD')

    def test_holds_when_worse_than_incumbent_even_under_gate(self):
        from app.services.distributional_model import backtest_verdict
        self.assertEqual(backtest_verdict(dist_ece=0.029, gauss_ece=0.01), 'HOLD')

    def test_holds_at_exact_gate_boundary_if_worse_than_incumbent(self):
        from app.services.distributional_model import backtest_verdict
        self.assertEqual(backtest_verdict(dist_ece=0.03, gauss_ece=0.02), 'HOLD')

    def test_promotes_at_exact_gate_boundary_if_better(self):
        from app.services.distributional_model import backtest_verdict
        self.assertEqual(backtest_verdict(dist_ece=0.03, gauss_ece=0.03), 'PROMOTE')


if __name__ == '__main__':
    import unittest
    unittest.main()
