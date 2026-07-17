"""Tests for the unified distributional predictor service (Task 5)."""

import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

import joblib
from sklearn.isotonic import IsotonicRegression

from app import db
from app.models import ModelMetadata
from tests.helpers import BaseTestCase
from tests.test_distributional_model import _seed_dist_logs


def _train_points_model():
    from app.services import distributional_model as dm

    for pid in ("701", "702", "703"):
        _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
    with patch.object(dm, "MIN_TRAIN_SAMPLES", 50):
        dm.train_distributional_model("player_points")


class TestPredictDistribution(BaseTestCase):

    def test_no_model_returns_none(self):
        from app.services.distributional_predictor import predict_distribution

        with self.app.app_context():
            result = predict_distribution("player_points", {"avg_stat_last_5": 20.0})
        self.assertIsNone(result)

    def test_unsupported_stat_type_returns_none(self):
        from app.services.distributional_predictor import predict_distribution

        with self.app.app_context():
            result = predict_distribution("player_assist_to_turnover_ratio", {})
        self.assertIsNone(result)

    def test_quantile_head_point_matches_interpolated_median(self):
        from app.services import distributional_model as dm
        from app.services.distribution import median_from_quantiles
        from app.services.distributional_predictor import predict_distribution

        with self.app.app_context():
            _train_points_model()
            with patch.object(dm, "MIN_TRAIN_SAMPLES", 50):
                rows = dm._build_dist_training_rows("player_points")
            _, _, features, _ = rows[-1]
            dist = predict_distribution("player_points", features)

        self.assertIsNotNone(dist)
        self.assertEqual(dist["kind"], "quantile")
        self.assertEqual(dist["quantile_values"], sorted(dist["quantile_values"]))
        self.assertAlmostEqual(
            dist["point"],
            median_from_quantiles(dist["alphas"], dist["quantile_values"]),
        )

    def test_poisson_head_uses_existing_point_model(self):
        from app.services import ml_model
        from app.services.distributional_predictor import predict_distribution

        with self.app.app_context():
            for pid in ("801", "802", "803"):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(ml_model, "MIN_TRAIN_SAMPLES", 50):
                ml_model.train_model("player_steals")
            with patch.object(ml_model, "MIN_TRAIN_SAMPLES", 50):
                rows = ml_model._build_training_rows("player_steals")
            _, _, features, _ = rows[-1]
            dist = predict_distribution("player_steals", features)

        self.assertIsNotNone(dist)
        self.assertEqual(dist["kind"], "poisson")
        self.assertGreater(dist["lam"], 0)
        self.assertEqual(dist["point"], dist["lam"])


class TestPredictProbOver(BaseTestCase):

    @patch("app.services.distributional_predictor.predict_distribution")
    def test_quantile_line_outside_support_returns_none(self, predict):
        from app.services.distributional_predictor import predict_prob_over
        predict.return_value = {
            "kind": "quantile", "point": 20.0,
            "alphas": [0.05, 0.95], "quantile_values": [10.0, 30.0],
        }
        with self.app.app_context():
            self.assertIsNone(predict_prob_over("player_points", {}, 60.5))

    @patch("app.services.distributional_predictor.load_calibrator", return_value=None)
    @patch("app.services.distributional_predictor.predict_distribution")
    def test_details_carry_distribution_point_alongside_prob(self, predict, _cal):
        from app.services.distributional_predictor import predict_prob_over_details
        predict.return_value = {
            "kind": "quantile", "point": 21.5,
            "alphas": [0.05, 0.5, 0.95], "quantile_values": [10.0, 21.5, 30.0],
        }
        with self.app.app_context():
            details = predict_prob_over_details("player_points", {}, 21.5)
        self.assertIsNotNone(details)
        self.assertEqual(details["point"], 21.5)
        self.assertEqual(details["kind"], "quantile")
        self.assertAlmostEqual(details["prob_over"], 0.5)

    @patch("app.services.distributional_predictor.ModelMetadata")
    def test_calibrator_load_is_cached_per_stat(self, metadata):
        from app.services import distributional_predictor as predictor
        predictor.load_calibrator.cache_clear()
        metadata.query.filter_by.return_value.first.return_value = None
        with self.app.app_context():
            predictor.load_calibrator("player_points")
            predictor.load_calibrator("player_points")
        metadata.query.filter_by.assert_called_once()

    def test_corrupt_active_calibrator_is_ignored(self):
        from app.services.distributional_predictor import load_calibrator
        from app.services.model_storage import persist_model_artifact

        with self.app.app_context():
            with tempfile.NamedTemporaryFile(suffix=".pkl") as artifact:
                artifact.write(b"not a joblib artifact")
                artifact.flush()
                artifact_path = persist_model_artifact(
                    artifact.name, "dist_calibrator_player_points_corrupt.pkl"
                )

            db.session.add(
                ModelMetadata(
                    model_name="dist_calibrator_player_points",
                    model_type="isotonic_calibrator",
                    version="corrupt-test",
                    file_path=artifact_path,
                    training_date=datetime.now(timezone.utc),
                    is_active=True,
                )
            )
            db.session.commit()

            self.assertIsNone(load_calibrator("player_points"))

    def test_no_model_returns_none(self):
        from app.services.distributional_predictor import predict_prob_over

        with self.app.app_context():
            result = predict_prob_over(
                "player_points", {"avg_stat_last_5": 20.0}, 20.5
            )
        self.assertIsNone(result)

    def test_monotone_non_increasing_in_line(self):
        from app.services import distributional_model as dm
        from app.services.distributional_predictor import predict_distribution, predict_prob_over

        with self.app.app_context():
            _train_points_model()
            with patch.object(dm, "MIN_TRAIN_SAMPLES", 50):
                rows = dm._build_dist_training_rows("player_points")
            _, _, features, _ = rows[-1]
            dist = predict_distribution("player_points", features)
            low, high = dist["quantile_values"][0], dist["quantile_values"][-1]
            probs = [
                predict_prob_over("player_points", features, line)
                for line in (low, (3 * low + high) / 4, (low + high) / 2,
                             (low + 3 * high) / 4, high)
            ]

        self.assertTrue(all(p is not None for p in probs))
        self.assertEqual(probs, sorted(probs, reverse=True))
        self.assertTrue(all(0.0 <= p <= 1.0 for p in probs))

    def test_applies_calibrator_when_one_is_active(self):
        from app.services import distributional_model as dm
        from app.services.distributional_predictor import (
            load_calibrator,
            predict_distribution,
            predict_prob_over,
        )
        from app.services.model_storage import persist_model_artifact

        with self.app.app_context():
            _train_points_model()
            with patch.object(dm, "MIN_TRAIN_SAMPLES", 50):
                rows = dm._build_dist_training_rows("player_points")
            _, _, features, _ = rows[-1]

            calibrator = IsotonicRegression(
                out_of_bounds="clip", y_min=0.0, y_max=1.0
            )
            calibrator.fit([0.0, 1.0], [0.5, 0.5])
            with tempfile.NamedTemporaryFile(suffix=".pkl") as artifact:
                joblib.dump(calibrator, artifact.name)
                artifact_path = persist_model_artifact(
                    artifact.name, "dist_calibrator_player_points_test.pkl"
                )
                ModelMetadata.query.filter_by(
                    model_name="dist_calibrator_player_points", is_active=True
                ).update({"is_active": False})
                db.session.add(
                    ModelMetadata(
                        model_name="dist_calibrator_player_points",
                        model_type="isotonic_calibrator",
                        version="test",
                        file_path=artifact_path,
                        training_date=datetime.now(timezone.utc),
                        is_active=True,
                    )
                )
                db.session.commit()

                load_calibrator.cache_clear()
                dist = predict_distribution("player_points", features)
                line = (dist["quantile_values"][0] + dist["quantile_values"][-1]) / 2
                calibrated = predict_prob_over("player_points", features, line)

        self.assertAlmostEqual(calibrated, 0.5, places=6)


if __name__ == "__main__":
    import unittest

    unittest.main()
