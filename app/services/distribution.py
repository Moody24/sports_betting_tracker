"""Pure quantile/CDF math for the Plan C distributional core (Increment 1).

No DB or model access here — this module only turns a predicted quantile
grid into a usable CDF / P(over line). Kept dependency-free (numpy only) so
it is trivially unit-testable and reusable from training, calibration, and
inference code alike.
"""

import math
from typing import List, Sequence

import numpy as np


def rectify_quantiles(quantile_values: Sequence[float]) -> List[float]:
    """Enforce a non-decreasing quantile function via cumulative maximum."""
    out: List[float] = []
    running_max = float("-inf")
    for value in quantile_values:
        running_max = max(running_max, float(value))
        out.append(running_max)
    return out


def quantile_at(
    alpha: float, alphas: Sequence[float], quantile_values: Sequence[float]
) -> float:
    """Linearly interpolate the quantile value at ``alpha`` and clamp endpoints."""
    return float(np.interp(alpha, list(alphas), list(quantile_values)))


def median_from_quantiles(
    alphas: Sequence[float], quantile_values: Sequence[float]
) -> float:
    """Return the interpolated median of the fitted quantile function."""
    return quantile_at(0.5, alphas, quantile_values)


def cdf_from_quantiles(
    line: float, alphas: Sequence[float], quantile_values: Sequence[float]
) -> float:
    """Interpolate CDF(line) from a non-decreasing quantile-value map."""
    return float(np.interp(line, list(quantile_values), list(alphas)))


def prob_over(
    line: float, alphas: Sequence[float], quantile_values: Sequence[float]
) -> float:
    """Return P(stat > line), clamped to the unit interval."""
    cdf = cdf_from_quantiles(line, alphas, quantile_values)
    return float(min(max(1.0 - cdf, 0.0), 1.0))


def prob_over_poisson(line: float, lam: float) -> float:
    """P(count stat > line) under Poisson(lam): 1 - PoissonCDF(floor(line), lam).

    Count-stat props (threes, steals, blocks) always quote half-integer
    lines (e.g. 1.5), so floor() never lands exactly on a support point and
    there is no tie ambiguity. lam < 0 is treated as lam == 0 (defensive —
    a trained regressor should never emit a negative mean).
    """
    from scipy.stats import poisson

    lam = max(float(lam), 0.0)
    k = math.floor(line)
    if k < 0:
        return 1.0
    return float(min(max(1.0 - poisson.cdf(k, lam), 0.0), 1.0))
