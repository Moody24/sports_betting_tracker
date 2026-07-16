"""Pure-math tests for app.services.distribution (Plan C Increment 1)."""

import unittest

from scipy.stats import poisson


ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
RAW_QUANTILES = [10, 12, 14, 13, 15, 16, 18, 17, 19, 20]
RECTIFIED = [10, 12, 14, 14, 15, 16, 18, 18, 19, 20]


class TestRectifyQuantiles(unittest.TestCase):
    def test_rectify_enforces_non_decreasing(self):
        from app.services.distribution import rectify_quantiles

        result = rectify_quantiles(RAW_QUANTILES)
        self.assertEqual(result, RECTIFIED)
        self.assertEqual(result, sorted(result))

    def test_rectify_already_monotone_is_unchanged(self):
        from app.services.distribution import rectify_quantiles

        values = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(rectify_quantiles(values), values)

    def test_rectify_single_value(self):
        from app.services.distribution import rectify_quantiles

        self.assertEqual(rectify_quantiles([7.5]), [7.5])


class TestQuantileAt(unittest.TestCase):
    def test_quantile_at_exact_knot(self):
        from app.services.distribution import quantile_at

        self.assertAlmostEqual(quantile_at(0.05, ALPHAS, RECTIFIED), 10.0)
        self.assertAlmostEqual(quantile_at(0.95, ALPHAS, RECTIFIED), 20.0)

    def test_quantile_at_interpolates_median(self):
        from app.services.distribution import quantile_at

        self.assertAlmostEqual(quantile_at(0.5, ALPHAS, RECTIFIED), 15.5)

    def test_quantile_at_clamps_outside_range(self):
        from app.services.distribution import quantile_at

        self.assertAlmostEqual(quantile_at(0.0, ALPHAS, RECTIFIED), 10.0)
        self.assertAlmostEqual(quantile_at(1.0, ALPHAS, RECTIFIED), 20.0)


class TestMedianFromQuantiles(unittest.TestCase):
    def test_median_matches_quantile_at_half(self):
        from app.services.distribution import median_from_quantiles

        self.assertAlmostEqual(median_from_quantiles(ALPHAS, RECTIFIED), 15.5)


class TestCdfFromQuantiles(unittest.TestCase):
    def test_cdf_at_knots(self):
        from app.services.distribution import cdf_from_quantiles

        self.assertAlmostEqual(cdf_from_quantiles(14, ALPHAS, RECTIFIED), 0.35)
        self.assertAlmostEqual(cdf_from_quantiles(10, ALPHAS, RECTIFIED), 0.05)
        self.assertAlmostEqual(cdf_from_quantiles(20, ALPHAS, RECTIFIED), 0.95)

    def test_cdf_clamps_below_and_above_range(self):
        from app.services.distribution import cdf_from_quantiles

        self.assertAlmostEqual(cdf_from_quantiles(5, ALPHAS, RECTIFIED), 0.05)
        self.assertAlmostEqual(cdf_from_quantiles(25, ALPHAS, RECTIFIED), 0.95)


class TestProbOver(unittest.TestCase):
    def test_prob_over_in_unit_interval(self):
        from app.services.distribution import prob_over

        for line in (-5, 0, 8, 14, 20, 30, 100):
            p = prob_over(line, ALPHAS, RECTIFIED)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_prob_over_monotone_non_increasing_in_line(self):
        from app.services.distribution import prob_over

        lines = [8, 10, 12, 14, 16, 18, 20, 22]
        probs = [prob_over(line, ALPHAS, RECTIFIED) for line in lines]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_prob_over_exact_values(self):
        from app.services.distribution import prob_over

        self.assertAlmostEqual(prob_over(14, ALPHAS, RECTIFIED), 0.65)
        self.assertAlmostEqual(prob_over(16, ALPHAS, RECTIFIED), 0.45)
        self.assertAlmostEqual(prob_over(10, ALPHAS, RECTIFIED), 0.95)


class TestProbOverPoisson(unittest.TestCase):
    def test_matches_scipy_poisson_cdf(self):
        from app.services.distribution import prob_over_poisson

        for line, lam in ((2.5, 3.4), (1.5, 1.2), (0.5, 0.8), (5.5, 2.0)):
            expected = 1.0 - poisson.cdf(int(line // 1), lam)
            self.assertAlmostEqual(prob_over_poisson(line, lam), expected)

    def test_half_integer_lines_no_tie_ambiguity(self):
        from app.services.distribution import prob_over_poisson

        # Props always quote half-integer lines for count stats; floor()
        # of a half-integer never lands on an integer support point twice.
        p_below = prob_over_poisson(1.5, 2.0)
        p_above = prob_over_poisson(2.5, 2.0)
        self.assertGreater(p_below, p_above)

    def test_in_unit_interval(self):
        from app.services.distribution import prob_over_poisson

        for line in (-1.5, 0.5, 3.5, 10.5):
            p = prob_over_poisson(line, 2.5)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_negative_lambda_treated_as_zero(self):
        from app.services.distribution import prob_over_poisson

        self.assertAlmostEqual(prob_over_poisson(0.5, -1.0), 0.0)


if __name__ == "__main__":
    unittest.main()
