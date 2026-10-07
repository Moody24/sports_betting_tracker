"""Calibration-only postprocessing; training and artifact writes are mocked."""

import json
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import numpy as np

from app import db
from app.models import ModelMetadata
from app.services import pick_quality_model as quality
from tests.helpers import BaseTestCase


class PickQualityPostprocessingTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.calibrator_fits = []
        self.base = Mock(feature_importances_=[1.0])
        fits = self.calibrator_fits

        class FakeCalibrator:
            def __init__(self, *_args, **_kwargs):
                pass

            def fit(self, features, labels):
                fits.append((features.copy(), labels.copy()))
                return self

            def predict_proba(self, features):
                probabilities = np.asarray(features)[:, 0]
                return np.column_stack([1 - probabilities, probabilities])

            def predict(self, features):
                return (self.predict_proba(features)[:, 1] >= 0.5).astype(int)

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('xgboost.XGBClassifier', return_value=self.base))
        stack.enter_context(patch('sklearn.calibration.CalibratedClassifierCV', FakeCalibrator))
        self.dump = stack.enter_context(patch('joblib.dump'))
        self.persist = stack.enter_context(patch.object(
            quality, 'persist_model_artifact', return_value='fixture-model.pkl'))
        stack.enter_context(patch.object(quality.os, 'makedirs'))
        stack.enter_context(patch.object(db.engine, 'dispose'))
        stack.enter_context(patch.object(quality, '_build_training_data', return_value=(
            [{'signal': 0.2}, {'signal': 0.8}], [0, 1], [None, None])))
        stack.enter_context(patch.object(quality, 'env_float', side_effect=lambda key, default:
                                        1.0 if key == 'MODEL2_PROBABILITY_SHRINK' else default))

    def train(self, labels):
        split = quality.TemporalPickSplit(
            X_fit=np.array([[0.2], [0.8]]), X_early=np.array([[0.3], [0.7]]),
            X_calibration=np.array([[0.8], [0.8], [0.8], [0.8]]),
            X_test=np.array([[0.2], [0.8], [0.2], [0.8]]),
            y_fit=np.array([0, 1]), y_early=np.array([0, 1]),
            y_calibration=np.array([1, 0, 1, 0]), y_test=np.array(labels),
            metadata={'split_method': 'three_way_date_cutoff'},
        )
        with patch.object(quality, '_prepare_training_data', return_value=split):
            result = quality.train_pick_quality_model()
        row = ModelMetadata.query.order_by(ModelMetadata.id.desc()).first()
        return result, json.loads(row.metadata_json) if row else None

    def test_test_labels_cannot_tune_bias_or_calibrator_and_metrics_use_deployed_output(self):
        first_result, first = self.train([1, 0, 0, 0])
        second_result, second = self.train([1, 1, 1, 0])
        self.assertEqual(first['calibration_bias'], second['calibration_bias'])
        self.assertAlmostEqual(first['calibration_bias'], 0.3)
        self.assertEqual(first['calibration_bias_source'], 'calibration_partition')
        self.assertEqual(first['postprocessing_version'], 'calibration_only_v1')
        np.testing.assert_array_equal(self.calibrator_fits[0][0], self.calibrator_fits[1][0])
        np.testing.assert_array_equal(self.calibrator_fits[0][1], self.calibrator_fits[1][1])
        expected = quality.compute_calibration_metrics([
            (quality._stabilize_probability(p, first), y)
            for p, y in zip([0.2, 0.8, 0.2, 0.8], [1, 0, 0, 0])
        ])
        self.assertAlmostEqual(first_result['brier'], expected['brier'])
        self.assertNotEqual(first_result['brier'], second_result['brier'])

    def test_legacy_test_derived_bias_is_not_applied(self):
        self.assertAlmostEqual(quality._stabilize_probability(0.7, {
            'probability_shrink': 1.0, 'calibration_bias': 0.2,
        }), 0.7)

    def test_calibrated_artifact_failure_cannot_activate_uncalibrated_model(self):
        old = ModelMetadata(
            model_name='pick_quality_nba', model_type='xgboost_classifier',
            version='existing', file_path='existing.pkl', training_date=datetime.now(timezone.utc),
            training_samples=400, is_active=True, metadata_json='{}',
        )
        db.session.add(old)
        db.session.commit()
        self.dump.side_effect = RuntimeError('fixture serialization failure')
        result, _ = self.train([1, 0, 0, 0])
        self.assertIn('error', result)
        self.base.save_model.assert_not_called()
        self.persist.assert_not_called()
        self.assertTrue(ModelMetadata.query.one().is_active)

    def test_artifact_storage_failure_does_not_create_active_metadata(self):
        self.persist.side_effect = RuntimeError('fixture storage unavailable')
        result, metadata = self.train([1, 0, 0, 0])
        self.assertIn('error', result)
        self.assertIsNone(metadata)
        self.base.save_model.assert_not_called()
        self.assertEqual(ModelMetadata.query.count(), 0)
