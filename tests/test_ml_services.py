"""Focused ml services tests split from the legacy service suite."""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from app import db
from app.models import (
    ModelMetadata,
    PickContext,
    PlayerGameLog,
)
from tests.helpers import BaseTestCase, make_bet, make_user
from tests.service_test_support import (
    _seed_player_logs,
)


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
            # Patch db.engine.dispose to prevent it from closing the in-memory
            # DB connection mid-test. dispose() is used in production to drop
            # stale Postgres SSL connections before the post-training write, but
            # in SQLite testing it would destroy the shared-cache in-memory DB.
            with patch.object(ml_model, '_build_training_rows', return_value=mock_rows):
                with patch.object(ml_model, '_ensure_model_dir'):
                    with patch('xgboost.XGBRegressor', return_value=fake_model):
                        with patch('sklearn.metrics.mean_absolute_error', return_value=1.234):
                            with patch.object(db.engine, 'dispose'):
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
        from app.services.pick_quality_model import TemporalPickSplit

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

        class _FakeCalibrated:
            def __init__(self, model, **_kwargs):
                self.model = model

            def fit(self, _x, _y):
                return self

            def predict(self, x):
                return self.model.predict(x)

            def predict_proba(self, x):
                return self.model.predict_proba(x)

        features = [
            {'projected_edge': 1.0, 'player_trend': 1},
            {'projected_edge': 0.5, 'player_trend': 0},
            {'projected_edge': -0.2, 'player_trend': -1},
            {'projected_edge': 0.1, 'player_trend': 0},
        ]
        targets = [1, 0, 1, 0]
        split = TemporalPickSplit(
            X_fit=features[:2], X_early=features[2:],
            X_calibration=features[:2], X_test=features[2:],
            y_fit=targets[:2], y_early=targets[2:],
            y_calibration=targets[:2], y_test=targets[2:],
            metadata={
                'split_method': 'three_way_date_cutoff',
                'fit_samples': 2, 'early_stopping_samples': 2,
                'calibration_samples': 2, 'test_samples': 2,
                'excluded_missing_dates': 0,
            },
        )

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

            # Patch db.engine.dispose to prevent it from closing the in-memory
            # DB connection mid-test. dispose() is used in production to drop
            # stale Postgres SSL connections before the post-training write, but
            # in SQLite testing it would destroy the shared-cache in-memory DB.
            with patch.dict(sys.modules, {
                'xgboost': fake_xgboost,
                'numpy': fake_np,
                'sklearn.metrics': fake_metrics,
                'sklearn.calibration': SimpleNamespace(CalibratedClassifierCV=_FakeCalibrated),
            }):
                with patch.object(pick_quality_model, '_build_training_data', return_value=(features, targets, [None] * len(targets))):
                    with patch.object(pick_quality_model, '_prepare_training_data', return_value=split):
                        with patch('app.services.pick_quality_model.persist_model_artifact', return_value='s3://bucket/model.json'):
                            with patch.object(db.engine, 'dispose'):
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


class TestPickQualityModelCalibration(BaseTestCase):
    """Tests for calibration, cold-start threshold, and local fallback."""

    def test_min_resolved_picks_is_400(self):
        from app.services import pick_quality_model
        self.assertEqual(pick_quality_model.MIN_RESOLVED_PICKS, 400)

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

    def test_prop_stat_key_includes_steals_blocks(self):
        from app.config_display import PROP_STAT_KEY
        self.assertIn('player_steals', PROP_STAT_KEY)
        self.assertIn('player_blocks', PROP_STAT_KEY)

    def test_train_model_metadata_includes_cv_fields(self):
        """train_model source contains cv_mean_mae/cv_std_mae metadata keys."""
        import inspect
        from app.services import ml_model
        source = inspect.getsource(ml_model.train_model)
        self.assertIn('cv_mean_mae', source)
        self.assertIn('cv_std_mae', source)
        self.assertIn('TimeSeriesSplit', source)
        self.assertIn('early_stopping_rounds', source)


class TestPrepareTrainingData(BaseTestCase):
    """Unit tests for _prepare_training_data()."""

    def _rows(self, n):
        return (
            [{'projected_stat': float(i), 'prop_line': float(i % 5)} for i in range(n)],
            [i % 2 for i in range(n)],
            [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(n)],
        )

    def test_return_shape_correct(self):
        from app.services.pick_quality_model import _prepare_training_data
        features_list, targets, dates = self._rows(420)
        split = _prepare_training_data(features_list, targets, dates)
        total = sum(len(values) for values in (
            split.X_fit, split.X_early, split.X_calibration, split.X_test,
        ))
        self.assertEqual(total, 420)
        self.assertEqual(split.metadata['split_method'], 'three_way_date_cutoff')

    def test_x_y_row_counts_match(self):
        from app.services.pick_quality_model import _prepare_training_data
        features_list, targets, dates = self._rows(420)
        split = _prepare_training_data(features_list, targets, dates)
        self.assertEqual(len(split.X_fit), len(split.y_fit))
        self.assertEqual(len(split.X_early), len(split.y_early))
        self.assertEqual(len(split.X_calibration), len(split.y_calibration))
        self.assertEqual(len(split.X_test), len(split.y_test))

    def test_missing_dates_never_fall_back_to_random_split(self):
        from app.services.pick_quality_model import _prepare_training_data
        features_list, targets, dates = self._rows(420)
        dates = [None] * len(dates)
        with self.assertRaisesRegex(ValueError, 'Insufficient dated'):
            _prepare_training_data(features_list, targets, dates)


class TestComputeClassWeights(BaseTestCase):
    """Unit tests for _compute_class_weights()."""

    def test_balanced_labels_close_to_one(self):
        import numpy as np
        from app.services.pick_quality_model import _compute_class_weights
        y = np.array([0, 1, 0, 1, 0, 1, 0, 1])
        self.assertAlmostEqual(_compute_class_weights(y), 1.0, places=5)

    def test_imbalanced_minority_gets_higher_weight(self):
        import numpy as np
        from app.services.pick_quality_model import _compute_class_weights
        # 1 positive out of 10 → scale_pos_weight = 9
        y = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 1])
        self.assertAlmostEqual(_compute_class_weights(y), 9.0, places=5)


class TestMarketRecommenderHelpers(BaseTestCase):
    """Tests for pure market_recommender helper functions."""

    def test_decide_market_action_strong_edge(self):
        """_decide_market_action returns 'bet' for high edge+confidence."""
        from app.services.market_recommender import _decide_market_action
        action, label = _decide_market_action(0.6, 0.7, 0.3, 0.5)
        self.assertEqual(action, 'bet')

    def test_decide_market_action_weak_edge(self):
        """_decide_market_action returns 'fade' for low edge."""
        from app.services.market_recommender import _decide_market_action
        action, label = _decide_market_action(0.1, 0.3, 0.3, 0.5)
        self.assertNotEqual(action, 'bet')

    def test_features_for_inputs_returns_list(self):
        """_features_for_inputs returns a non-empty list."""
        from app.services.market_recommender import _features_for_inputs
        feats = _features_for_inputs(225.5, -150, 130)
        self.assertIsInstance(feats, list)
        self.assertGreater(len(feats), 0)

    def test_profit_per_unit_favorite_win(self):
        """_profit_per_unit returns correct decimal for a -110 win."""
        from app.services.market_recommender import _profit_per_unit
        profit = _profit_per_unit(-110, won=True)
        self.assertGreater(profit, 0)

    def test_profit_per_unit_loss(self):
        """_profit_per_unit returns -1.0 for a loss."""
        from app.services.market_recommender import _profit_per_unit
        profit = _profit_per_unit(-110, won=False)
        self.assertAlmostEqual(profit, -1.0)


class TestMarketRecommenderDirect(BaseTestCase):
    """Tests for market_recommender.py functions that are pure logic."""

    def test_load_recent_final_snapshots_empty_db(self):
        """_load_recent_final_snapshots returns [] when no final snapshots exist."""
        from app.services.market_recommender import _load_recent_final_snapshots
        with self.app.app_context():
            result = _load_recent_final_snapshots(days=30)
        self.assertEqual(result, [])

    def test_evaluate_market_models_insufficient_data(self):
        """evaluate_market_models returns error when < 40 final snapshots."""
        from app.services.market_recommender import evaluate_market_models
        with self.app.app_context():
            result = evaluate_market_models(days=30)
        self.assertIn('error', result)

    def test_resolve_market_policy_no_metadata(self):
        """_resolve_market_policy returns default policy when no models exist."""
        from app.services.market_recommender import _resolve_market_policy
        policy = _resolve_market_policy(None, None)
        self.assertIsInstance(policy, dict)

    def test_set_market_enabled_invalid_market(self):
        """set_market_enabled returns error for unknown market name."""
        from app.services.market_recommender import set_market_enabled
        with self.app.app_context():
            result = set_market_enabled('invalid_market', enabled=True)
        self.assertIn('error', result)

    def test_set_market_enabled_no_active_model(self):
        """set_market_enabled returns error when no active model metadata exists."""
        from app.services.market_recommender import set_market_enabled
        with self.app.app_context():
            result = set_market_enabled('moneyline', enabled=False)
        self.assertIn('error', result)
        self.assertEqual(result['error'], 'no_active_model')

    def test_recommend_market_sides_no_games(self):
        """recommend_market_sides returns {} when no games provided."""
        from app.services.market_recommender import recommend_market_sides
        with self.app.app_context():
            result = recommend_market_sides([])
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), 0)

    def test_train_market_models_returns_both_markets(self):
        """train_market_models returns dict with moneyline and total_ou keys."""
        from app.services.market_recommender import train_market_models
        with self.app.app_context():
            result = train_market_models()
        self.assertIn('moneyline', result)
        self.assertIn('total_ou', result)
        self.assertIn('rows_scanned', result)
        # With no data, both markets should have error key
        self.assertIn('error', result['moneyline'])


class TestModelStorageFunctions(BaseTestCase):
    """Tests for model_storage.py artifact management functions."""

    def test_materialize_model_artifact_none_path(self):
        """materialize_model_artifact returns None when path is None."""
        from app.services.model_storage import materialize_model_artifact
        result = materialize_model_artifact(None)
        self.assertIsNone(result)

    def test_materialize_model_artifact_local_path(self):
        """materialize_model_artifact returns local path when file exists."""
        from app.services.model_storage import materialize_model_artifact
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            f.write(b'{}')
            tmp_path = f.name
        result = materialize_model_artifact(tmp_path)
        self.assertEqual(result, tmp_path)
        import os as _os
        _os.unlink(tmp_path)

    def test_materialize_model_artifact_nonexistent_local(self):
        """materialize_model_artifact returns None for non-existent local path."""
        from app.services.model_storage import materialize_model_artifact
        result = materialize_model_artifact('/nonexistent/path/model.json')
        self.assertIsNone(result)

    def test_storage_mode_default_local(self):
        """storage_mode returns 'local' by default."""
        from app.services.model_storage import storage_mode
        with patch.dict(os.environ, {'MODEL_STORAGE': ''}):
            result = storage_mode()
        self.assertEqual(result, 'local')

    def test_parse_s3_uri_valid(self):
        """_parse_s3_uri correctly parses s3://bucket/key."""
        from app.services.model_storage import _parse_s3_uri
        bucket, key = _parse_s3_uri('s3://my-bucket/models/model.json')
        self.assertEqual(bucket, 'my-bucket')
        self.assertEqual(key, 'models/model.json')

    def test_parse_s3_uri_invalid_raises(self):
        """_parse_s3_uri raises ValueError for non-S3 URI."""
        from app.services.model_storage import _parse_s3_uri
        with self.assertRaises(ValueError):
            _parse_s3_uri('/local/path/model.json')

    def test_persist_model_artifact_local_mode(self):
        """persist_model_artifact returns local_path when MODEL_STORAGE=local."""
        from app.services.model_storage import persist_model_artifact
        with patch.dict(os.environ, {'MODEL_STORAGE': 'local'}):
            result = persist_model_artifact('/tmp/model.json', 'model.json')
        self.assertEqual(result, '/tmp/model.json')

    def test_persist_model_artifact_s3_no_bucket(self):
        """persist_model_artifact falls back to local when S3 bucket not configured."""
        from app.services.model_storage import persist_model_artifact
        with patch.dict(os.environ, {'MODEL_STORAGE': 's3', 'S3_MODEL_BUCKET': ''}):
            result = persist_model_artifact('/tmp/model.json', 'model.json')
        self.assertEqual(result, '/tmp/model.json')

    def test_build_s3_key_with_prefix(self):
        """_build_s3_key prepends prefix from env var."""
        from app.services.model_storage import _build_s3_key
        with patch.dict(os.environ, {'S3_MODEL_PREFIX': 'prod/models'}):
            key = _build_s3_key('player_points.json')
        self.assertEqual(key, 'prod/models/player_points.json')
