"""Walk-forward OOF isotonic calibration for distributional P(over) heads.

Fits/applies an isotonic calibrator on pooled (raw model P(over), realized
over-or-under) pairs. The OOF pairs themselves come from either a
quantile-head holdout (collect_oof_pairs_quantile) or a Poisson-head
holdout (collect_oof_pairs_poisson) — both generate synthetic candidate
lines around each held-out row's own point estimate, since historical
PlayerGameLog rows have no associated sportsbook line.
"""

from typing import List, Sequence, Tuple

from sklearn.isotonic import IsotonicRegression

from app.services.distribution import median_from_quantiles, prob_over, prob_over_poisson

CALIBRATION_LINE_OFFSET_FRACTIONS: Tuple[float, ...] = (-0.9, -0.6, -0.3, 0.0, 0.3, 0.6, 0.9)


def collect_oof_pairs_quantile(
    oof_rows: Sequence[Tuple[Sequence[float], Sequence[float], float]],
    offset_fractions: Sequence[float] = CALIBRATION_LINE_OFFSET_FRACTIONS,
) -> List[Tuple[float, float]]:
    """Build (raw P(over), realized_over) pairs from quantile-head OOF rows."""
    pairs: List[Tuple[float, float]] = []
    for alphas, qvals, realized in oof_rows:
        median = median_from_quantiles(alphas, qvals)
        half_spread = max((qvals[-1] - qvals[0]) / 2.0, 0.5)
        for fraction in offset_fractions:
            line = median + fraction * half_spread
            pairs.append((prob_over(line, alphas, qvals), 1.0 if realized > line else 0.0))
    return pairs


def collect_oof_pairs_poisson(
    oof_rows: Sequence[Tuple[float, float]],
    offset_fractions: Sequence[float] = CALIBRATION_LINE_OFFSET_FRACTIONS,
) -> List[Tuple[float, float]]:
    """Build (raw P(over), realized_over) pairs from Poisson-head OOF rows."""
    import math

    pairs: List[Tuple[float, float]] = []
    for lam, realized in oof_rows:
        half_spread = max(lam, 1.0)
        for fraction in offset_fractions:
            candidate = lam + fraction * half_spread
            line = max(0.5, math.floor(candidate) + 0.5)
            pairs.append((prob_over_poisson(line, lam), 1.0 if realized > line else 0.0))
    return pairs


def fit_isotonic_calibrator(pairs: Sequence[Tuple[float, float]]) -> IsotonicRegression:
    """Fit an isotonic mapping from raw to calibrated P(over)."""
    if not pairs:
        raise ValueError("Cannot fit a calibrator on an empty pair set")
    calibrator = IsotonicRegression(out_of_bounds='clip', y_min=0.0, y_max=1.0)
    calibrator.fit([p for p, _ in pairs], [y for _, y in pairs])
    return calibrator


def apply_calibrator(calibrator: IsotonicRegression, p_raw: float) -> float:
    """Apply a fitted calibrator, clamped to the unit interval."""
    calibrated = float(calibrator.predict([p_raw])[0])
    return min(max(calibrated, 0.0), 1.0)
