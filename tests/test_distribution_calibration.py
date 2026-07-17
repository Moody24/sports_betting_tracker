"""Tests for app.services.distribution_calibration (Plan C Increment 1)."""

import unittest

ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]


class TestCollectOofPairsQuantile(unittest.TestCase):

    def test_produces_one_pair_per_offset_per_row(self):
        from app.services.distribution_calibration import (
            CALIBRATION_LINE_OFFSET_FRACTIONS,
            collect_oof_pairs_quantile,
        )

        rectified = [10, 12, 14, 14, 15, 16, 18, 18, 19, 20]
        oof_rows = [(ALPHAS, rectified, 22.0), (ALPHAS, rectified, 8.0)]
        pairs = collect_oof_pairs_quantile(oof_rows)
        self.assertEqual(len(pairs), len(oof_rows) * len(CALIBRATION_LINE_OFFSET_FRACTIONS))
        for p, y in pairs:
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)
            self.assertIn(y, (0.0, 1.0))

    def test_realized_above_all_candidate_lines_gives_all_over_labels(self):
        from app.services.distribution_calibration import collect_oof_pairs_quantile

        rectified = [10, 12, 14, 14, 15, 16, 18, 18, 19, 20]
        pairs = collect_oof_pairs_quantile([(ALPHAS, rectified, 22.0)])
        self.assertTrue(all(y == 1.0 for _, y in pairs))


class TestCollectOofPairsPoisson(unittest.TestCase):

    def test_produces_one_pair_per_offset_per_row(self):
        from app.services.distribution_calibration import (
            CALIBRATION_LINE_OFFSET_FRACTIONS,
            collect_oof_pairs_poisson,
        )

        oof_rows = [(2.0, 5.0), (1.0, 0.0)]
        pairs = collect_oof_pairs_poisson(oof_rows)
        self.assertEqual(len(pairs), len(oof_rows) * len(CALIBRATION_LINE_OFFSET_FRACTIONS))
        for p, y in pairs:
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)


class TestFitApplyCalibrator(unittest.TestCase):

    def _miscalibrated_pairs(self):
        xs = [0.9] * 10 + [0.2] * 10
        ys = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0] + [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        return list(zip(xs, ys))

    def test_fit_raises_on_empty_pairs(self):
        from app.services.distribution_calibration import fit_isotonic_calibrator

        with self.assertRaises(ValueError):
            fit_isotonic_calibrator([])

    def test_fit_and_apply_learns_monotone_correction(self):
        from app.services.distribution_calibration import apply_calibrator, fit_isotonic_calibrator

        calibrator = fit_isotonic_calibrator(self._miscalibrated_pairs())
        self.assertAlmostEqual(apply_calibrator(calibrator, 0.9), 0.5, places=6)
        self.assertAlmostEqual(apply_calibrator(calibrator, 0.2), 0.5, places=6)

    def test_apply_calibrator_clamped_to_unit_interval(self):
        from app.services.distribution_calibration import apply_calibrator, fit_isotonic_calibrator

        calibrator = fit_isotonic_calibrator(self._miscalibrated_pairs())
        p = apply_calibrator(calibrator, 5.0)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_calibration_improves_ece_on_miscalibrated_fixture(self):
        from app.services.distribution_calibration import apply_calibrator, fit_isotonic_calibrator
        from app.services.pick_quality_model import compute_calibration_metrics

        pairs = self._miscalibrated_pairs()
        before = compute_calibration_metrics(pairs, bins=5)
        self.assertAlmostEqual(before['ece'], 0.35, places=4)

        calibrator = fit_isotonic_calibrator(pairs)
        calibrated_pairs = [(apply_calibrator(calibrator, p), y) for p, y in pairs]
        after = compute_calibration_metrics(calibrated_pairs, bins=5)
        self.assertAlmostEqual(after['ece'], 0.0, places=4)
        self.assertLess(after['ece'], before['ece'])

    def test_metrics_clip_exact_probability_endpoints_for_logloss(self):
        from app.services.pick_quality_model import compute_calibration_metrics

        metrics = compute_calibration_metrics([(1.0, 1), (0.0, 0)], bins=5)

        self.assertGreaterEqual(metrics['logloss'], 0.0)
        self.assertLess(metrics['logloss'], 0.001)


if __name__ == '__main__':
    unittest.main()
