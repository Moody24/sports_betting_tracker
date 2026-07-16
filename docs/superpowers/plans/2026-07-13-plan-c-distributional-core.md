# Plan C — Increment 1: Distributional Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synthetic `Normal(projection, std_dev)` CDF in `ValueDetector._model_prob_over` with a calibrated predictive distribution per (player, stat) — multi-quantile XGBoost for points/rebounds/assists/PRA, Poisson CDF for threes/steals/blocks, isotonic-calibrated — behind a new `USE_DISTRIBUTIONAL_MODEL` flag that defaults to today's exact behavior.

**Architecture:** Two new pure-math modules (`distribution.py` for quantile/Poisson CDF math, `distribution_calibration.py` for isotonic fit/apply) sit under a new training module (`distributional_model.py`) that trains multi-quantile XGBoost heads for continuous stats and reuses the existing Poisson point regressors for count stats, and a unifying inference module (`distributional_predictor.py`) that loads whichever head applies plus its calibrator. `ValueDetector._model_prob_over` gains one new branch consuming `distributional_predictor.predict_prob_over`; the CLI's `retrain` command is extended and a new `backtest` command gates promotion on calibration quality. Every new artifact lives under `dist_<stat>` / `dist_calibrator_<stat>` `ModelMetadata` rows so the incumbent point models are never touched.

**Tech Stack:** Python 3, Flask, SQLAlchemy, XGBoost 2.1.3 (`reg:quantileerror` multi-quantile single-booster, confirmed working with `eval_set`/`early_stopping_rounds`/`sample_weight` in this environment), scipy 1.14.1 (`scipy.stats.poisson`), scikit-learn 1.5.2 (`sklearn.isotonic.IsotonicRegression`), joblib, `unittest`.

## Global Constraints

- Test runner: `SECRET_KEY=test python -m unittest tests.<module> -v` (unittest, NOT pytest). Full suite: `SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v` then `python -m coverage report --include="app/*"`.
- All test runs are FOREGROUND. Tests never hit the network or the real `instance/app.db` — offline synthetic fixtures with fixed seeds, following the existing `_seed_player_logs` / `BaseTestCase` pattern in `tests/helpers.py` and `tests/test_services.py`.
- Run `ruff check .` and `SECRET_KEY=test bandit -q -r app -x tests -ll`-equivalent (`bandit -q -r app -x tests -ll`) before every commit — CI enforces both on push. 80% coverage gate (`--include="app/*"`).
- ET timezone (`from app.utils.time_helpers import ET`) for any date logic; this increment adds none beyond what's already threaded through `ml_feature_builder`.
- Commits are conventional-format and MUST NOT include a `Co-Authored-By` trailer (project owner's explicit standing preference).
- New env flag `USE_DISTRIBUTIONAL_MODEL` (default `false`), read the same way `ProjectionEngine._use_ml_projections()` reads `USE_ML_PROJECTIONS` (`os.getenv(..., 'false').lower() == 'true'`) — see `app/services/projection_engine.py:435-436`.
- Quantile grid (from the approved design spec, `docs/superpowers/specs/2026-07-13-plan-c-distributional-core-design.md`): `QUANTILE_ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]`. XGBoost objective `reg:quantileerror` with `quantile_alpha=QUANTILE_ALPHAS` on a single booster (confirmed in this repo's `.venv`: `model.predict(X)` returns shape `(n, 10)` in the same column order as `quantile_alpha`, and `early_stopping_rounds` + `sample_weight` both work with this objective).
- Continuous heads (points, rebounds, assists, PRA): monotone-rectify predicted quantiles (elementwise cumulative max over the ascending-alpha grid) before any CDF interpolation. `P(over line) = 1 − CDF(line)`, clamped to `[0, 1]`. The distribution **median** (`alpha=0.5`, interpolated — 0.5 is not itself a trained node in the grid above) is the point projection.
- PRA is trained **directly** on realized `pts+reb+ast`, not summed from component models. This retires `COMBO_PROP_BIAS_CORRECTION` (`app/services/projection_engine.py:52-54`) on the distributional path only — the heuristic/summed path is untouched.
- Count heads (threes, steals, blocks): reuse the existing `count:poisson` point regressors from `app/services/ml_model.py` (`STAT_TRAINING_CONFIG`, `ml_model.py:38-45`) unchanged. `P(over line) = 1 − PoissonCDF(⌊line⌋, λ)` where λ is the regressor's predicted mean.
- Features: reuse `app/services/ml_feature_builder.py`'s `FEATURE_KEYS` (30 keys) and `build_ml_features_from_history` **completely unchanged** — no new features, no scenario features (blocked/deferred per spec Non-Goals).
- Calibration: fresh walk-forward out-of-fold isotonic regression per stat, fit on `(raw P(over), realized-over)` pairs, applied at inference before `P(over)` reaches the staking path. Reuse (via extraction) the reliability/Brier/log-loss/ECE math already in `app/services/pick_quality_model.get_calibration_report` (`pick_quality_model.py:613`).
- Model artifacts stored via the existing `app/services/model_storage.py` (`persist_model_artifact` / `materialize_model_artifact`) under new names — `dist_<stat_type>` (quantile heads) and `dist_calibrator_<stat_type>` (all six heads' calibrators) — so `projection_<stat_type>` and `pick_quality_nba` are never overwritten.
- Calibrators are serialized with `joblib.dump`/loaded with `joblib.load`, matching the existing pattern already used for calibrated Model 2 artifacts in `app/services/pick_quality_model.py` (`CalibratedClassifierCV` via `joblib`). This is safe here: every artifact is produced by this app's own training pipeline and stored through `model_storage.py` (local disk or a private S3 bucket the app controls) — never deserialized from user input or an external/untrusted source. Do not extend this pattern to anything that reads pickle/joblib data from outside the training pipeline.
- Non-Goals (binding — do not implement): scenario/`agreement_score` features, copula/SGP joint combos, CLV capture, Kelly staking, live-context builder, any UI/template changes.
- Success gates (from the Phase 1 spec, restated in the design spec): out-of-fold ECE ≤ 0.03; full retrain < 30 minutes.

---

### Task 1: Quantile → CDF utilities

**Files:**
- Create: `app/services/distribution.py`
- Test: `tests/test_distribution.py`

**Interfaces:**
- Consumes: nothing (pure math; only `numpy` and the stdlib).
- Produces (used by Tasks 3, 4, 5, 7):
  - `rectify_quantiles(quantile_values: Sequence[float]) -> List[float]`
  - `quantile_at(alpha: float, alphas: Sequence[float], quantile_values: Sequence[float]) -> float`
  - `median_from_quantiles(alphas: Sequence[float], quantile_values: Sequence[float]) -> float`
  - `cdf_from_quantiles(line: float, alphas: Sequence[float], quantile_values: Sequence[float]) -> float`
  - `prob_over(line: float, alphas: Sequence[float], quantile_values: Sequence[float]) -> float`

- [ ] **Step 1: Write the failing test**

Create `tests/test_distribution.py`:

```python
"""Pure-math tests for app.services.distribution (Plan C Increment 1)."""

import unittest

ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
RAW_QUANTILES = [10, 12, 14, 13, 15, 16, 18, 17, 19, 20]  # not monotone (13 < 14, 17 < 18)
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
        # alpha=0.5 sits halfway between the 0.45 (15) and 0.55 (16) knots.
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
        probs = [prob_over(ln, ALPHAS, RECTIFIED) for ln in lines]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_prob_over_exact_values(self):
        from app.services.distribution import prob_over
        self.assertAlmostEqual(prob_over(14, ALPHAS, RECTIFIED), 0.65)
        self.assertAlmostEqual(prob_over(16, ALPHAS, RECTIFIED), 0.45)
        self.assertAlmostEqual(prob_over(10, ALPHAS, RECTIFIED), 0.95)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `SECRET_KEY=test python -m unittest tests.test_distribution -v`
Expected: `ModuleNotFoundError: No module named 'app.services.distribution'` (or `ImportError`) for every test.

- [ ] **Step 3: Write minimal implementation**

Create `app/services/distribution.py`:

```python
"""Pure quantile/CDF math for the Plan C distributional core (Increment 1).

No DB or model access here — this module only turns a predicted quantile
grid (from a multi-quantile XGBoost booster, see distributional_model.py)
into a usable CDF / P(over line). Kept dependency-free (numpy only) so it
is trivially unit-testable and reusable from training, calibration, and
inference code alike.
"""

from typing import List, Sequence

import numpy as np


def rectify_quantiles(quantile_values: Sequence[float]) -> List[float]:
    """Enforce a non-decreasing quantile function via elementwise cumulative max.

    XGBoost's multi-quantile ``reg:quantileerror`` booster does not
    guarantee monotone output across the alpha grid (each quantile is fit
    independently). ``quantile_values`` must already be ordered by
    ascending alpha (this is how ``XGBRegressor.predict()`` returns them
    when trained with a ``quantile_alpha`` list). Returns a new list of the
    same length where element i is >= all elements before it.
    """
    out: List[float] = []
    running_max = float('-inf')
    for v in quantile_values:
        running_max = max(running_max, float(v))
        out.append(running_max)
    return out


def quantile_at(alpha: float, alphas: Sequence[float], quantile_values: Sequence[float]) -> float:
    """Interpolate the fitted quantile function's value at cumulative probability ``alpha``.

    ``alphas`` must be ascending; ``quantile_values`` are the corresponding
    (already rectified) values. Linear interpolation; clamps to the nearest
    endpoint value when ``alpha`` falls outside ``[alphas[0], alphas[-1]]``.
    """
    return float(np.interp(alpha, list(alphas), list(quantile_values)))


def median_from_quantiles(alphas: Sequence[float], quantile_values: Sequence[float]) -> float:
    """The distribution median (alpha=0.5) — used as the point projection.

    Note: the design's trained grid (0.05, 0.15, ..., 0.95) has no node at
    exactly 0.5; the median is obtained by interpolating the fitted
    quantile function, not by reading off a trained node.
    """
    return quantile_at(0.5, alphas, quantile_values)


def cdf_from_quantiles(line: float, alphas: Sequence[float], quantile_values: Sequence[float]) -> float:
    """Interpolate CDF(line) from the (quantile-value -> alpha) map.

    ``quantile_values`` must be non-decreasing (call ``rectify_quantiles``
    first). Values of ``line`` outside
    ``[quantile_values[0], quantile_values[-1]]`` clamp to the nearest
    endpoint alpha — there is no information beyond the extreme trained
    quantiles.
    """
    return float(np.interp(line, list(quantile_values), list(alphas)))


def prob_over(line: float, alphas: Sequence[float], quantile_values: Sequence[float]) -> float:
    """P(stat > line) = 1 - CDF(line), clamped to [0, 1]."""
    cdf = cdf_from_quantiles(line, alphas, quantile_values)
    return float(min(max(1.0 - cdf, 0.0), 1.0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_distribution -v`
Expected: all tests in `TestRectifyQuantiles`, `TestQuantileAt`, `TestMedianFromQuantiles`, `TestCdfFromQuantiles`, `TestProbOver` PASS (`TestProbOver` will show 2 failures — `test_prob_over_monotone...` and `test_prob_over_exact_values` — until Step 3's file exists; after Step 3 all pass. `prob_over_poisson` does not exist yet — that's Task 2, not referenced here).

- [ ] **Step 5: Lint**

Run: `ruff check app/services/distribution.py tests/test_distribution.py`
Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git add app/services/distribution.py tests/test_distribution.py
git commit -m "feat: add quantile-to-CDF distribution utilities (Plan C Increment 1)"
```

---

### Task 2: Poisson count-head P(over)

**Files:**
- Modify: `app/services/distribution.py`
- Test: `tests/test_distribution.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Tasks 4, 5, 7): `prob_over_poisson(line: float, lam: float) -> float`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_distribution.py` (add the import at the top of the file alongside `unittest`):

```python
from scipy.stats import poisson


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `SECRET_KEY=test python -m unittest tests.test_distribution -v`
Expected: `TestProbOverPoisson` fails with `AttributeError: module 'app.services.distribution' has no attribute 'prob_over_poisson'` (import happens inside each test method, matching the existing test style in this class).

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/distribution.py` (after `prob_over`, add `import math` to the top-of-file imports alongside `numpy`):

```python
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
```

And add `import math` at the top of `app/services/distribution.py`:

```python
import math
from typing import List, Sequence

import numpy as np
```

- [ ] **Step 4: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_distribution -v`
Expected: all tests pass, including `TestProbOverPoisson`.

- [ ] **Step 5: Lint**

Run: `ruff check app/services/distribution.py tests/test_distribution.py`
Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git add app/services/distribution.py tests/test_distribution.py
git commit -m "feat: add Poisson P(over) for count-stat distributional heads"
```

---

### Task 3: Multi-quantile model training (points/rebounds/assists/PRA)

**Files:**
- Create: `app/services/distributional_model.py`
- Test: `tests/test_distributional_model.py`

**Interfaces:**
- Consumes:
  - `app.services.distribution.rectify_quantiles`, `median_from_quantiles` (Task 1)
  - `app.services.ml_model._build_defense_lookup() -> dict`, `_build_game_total_lookup() -> dict`, `_check_training_data_quality(all_logs: list) -> dict`, `_ensure_model_dir() -> None`, `MIN_TRAIN_SAMPLES: int`, `MODEL_DIR: str` (all existing, `app/services/ml_model.py`)
  - `app.services.ml_feature_builder.build_ml_features_from_history(...)`, `build_team_game_aggregates(rows) -> tuple[dict, dict]` (existing, unchanged)
  - `app.services.model_storage.persist_model_artifact(local_path, filename) -> str`
- Produces (used by Tasks 5, 6, 7):
  - `QUANTILE_ALPHAS: List[float]` = `[0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]`
  - `DIST_STAT_KEY_MAP: Dict[str, str]` = `{'player_points': 'pts', 'player_rebounds': 'reb', 'player_assists': 'ast', 'player_points_rebounds_assists': 'pra'}`
  - `DIST_STAT_TYPES: List[str]` = `list(DIST_STAT_KEY_MAP.keys())`
  - `POISSON_DIST_STAT_TYPES: List[str]` = `['player_threes', 'player_steals', 'player_blocks']`
  - `class _PRALogProxy` — wraps a `PlayerGameLog` row, exposes `.pra` (pts+reb+ast), delegates everything else
  - `wrap_pra_logs(logs: list) -> list`
  - `_date_cutoff_split(rows: list, frac: float = 0.8) -> tuple[list[int], list[int], str, date|None]`
  - `_build_dist_training_rows(stat_type: str) -> list[tuple[date, str, dict, float]]`
  - `train_distributional_model(stat_type: str) -> dict` — keys: `stat_type`, `val_mae`, `train_samples`, `val_samples`, `model_path`, or `{'error': str, ...}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_distributional_model.py`:

```python
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


if __name__ == '__main__':
    import unittest
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `SECRET_KEY=test python -m unittest tests.test_distributional_model -v`
Expected: every test fails with `ModuleNotFoundError: No module named 'app.services.distributional_model'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/services/distributional_model.py`:

```python
"""Multi-quantile XGBoost training for the Plan C distributional core.

Increment 1 covers the continuous-stat heads (points, rebounds, assists,
and PRA trained directly on realized pts+reb+ast). Count stats (threes,
steals, blocks) reuse the existing count:poisson point regressors from
app/services/ml_model.py unchanged — see distributional_predictor.py.

Training rows are built the same way app.services.ml_model._build_training_rows
builds them (same sliding window, same defense/game-total lookups, same
ml_feature_builder.build_ml_features_from_history call), so train/inference
feature parity with the point model is preserved. The only new mechanism is
_PRALogProxy, which lets the unchanged feature builder compute
avg_stat_last_5-style features against realized PRA instead of a single
stored column.
"""

import json
import logging
import os
from datetime import date as date_type, datetime, timezone

from app import db
from app.models import ModelMetadata, PlayerGameLog
from app.services.distribution import median_from_quantiles, rectify_quantiles
from app.services.ml_feature_builder import build_ml_features_from_history, build_team_game_aggregates
from app.services.ml_model import (
    MIN_TRAIN_SAMPLES,
    MODEL_DIR,
    _build_defense_lookup,
    _build_game_total_lookup,
    _check_training_data_quality,
    _ensure_model_dir,
)
from app.services.model_storage import persist_model_artifact

logger = logging.getLogger(__name__)

QUANTILE_ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]

DIST_STAT_KEY_MAP = {
    'player_points': 'pts',
    'player_rebounds': 'reb',
    'player_assists': 'ast',
    'player_points_rebounds_assists': 'pra',
}
DIST_STAT_TYPES = list(DIST_STAT_KEY_MAP.keys())

# Count stats keep their existing count:poisson point regressor (ml_model.py)
# as the raw model; Increment 1 only adds a calibrator on top (see Task 7).
POISSON_DIST_STAT_TYPES = ['player_threes', 'player_steals', 'player_blocks']


class _PRALogProxy:
    """Wraps a PlayerGameLog row, exposing a computed ``pra`` attribute
    (pts+reb+ast) while delegating every other attribute unchanged.

    ml_feature_builder.build_ml_features_from_history is reused UNCHANGED
    for the PRA head (per the design spec); this proxy is the only new code
    needed to make its stat_key-driven features (avg_stat_last_5,
    std_stat_last_5, home/away/context splits, opponent history) operate on
    realized PRA instead of a single stored column.
    """

    def __init__(self, log):
        self._log = log

    @property
    def pra(self) -> float:
        return (
            float(getattr(self._log, 'pts', 0.0) or 0.0)
            + float(getattr(self._log, 'reb', 0.0) or 0.0)
            + float(getattr(self._log, 'ast', 0.0) or 0.0)
        )

    def __getattr__(self, name):
        return getattr(self._log, name)


def wrap_pra_logs(logs: list) -> list:
    """Wrap plain PlayerGameLog rows so stat_key='pra' features compute correctly."""
    return [_PRALogProxy(g) for g in logs]


def _date_cutoff_split(rows: list, frac: float = 0.8):
    """Chronological holdout split, mirroring ml_model.train_model's
    date_cutoff method (app/services/ml_model.py:356-378).

    ``rows`` are ``(date, player_id, features, target)`` tuples. Returns
    ``(train_idx, val_idx, split_method, cutoff_date)``. Falls back to a
    plain index split when fewer than 2 unique dates are present (e.g. tiny
    test fixtures).
    """
    unique_dates = sorted({r[0] for r in rows if r[0] is not None})
    train_idx: list = []
    val_idx: list = []
    cutoff_date = None
    split_method = 'date_cutoff'

    if len(unique_dates) >= 2:
        cutoff_idx = int(len(unique_dates) * frac) - 1
        cutoff_idx = max(0, min(cutoff_idx, len(unique_dates) - 2))
        cutoff_date = unique_dates[cutoff_idx]
        for idx, row in enumerate(rows):
            if row[0] is not None and row[0] <= cutoff_date:
                train_idx.append(idx)
            else:
                val_idx.append(idx)

    if not train_idx or len(val_idx) < 1:
        split_method = 'index_fallback'
        split_idx = int(len(rows) * frac)
        split_idx = min(max(split_idx, 1), len(rows) - 1)
        train_idx = list(range(split_idx))
        val_idx = list(range(split_idx, len(rows)))

    return train_idx, val_idx, split_method, cutoff_date


def _build_dist_training_rows(stat_type: str) -> list:
    """Dated training rows for one distributional stat type.

    Mirrors ml_model._build_training_rows (app/services/ml_model.py:190-255)
    exactly, but resolves the target (and the stat_key handed to the
    feature builder) via DIST_STAT_KEY_MAP, wrapping logs with
    _PRALogProxy for the PRA head. Returns [] for unsupported stat types or
    insufficient data.
    """
    stat_key = DIST_STAT_KEY_MAP.get(stat_type)
    if not stat_key:
        return []

    all_logs = (
        PlayerGameLog.query
        .order_by(PlayerGameLog.player_id, PlayerGameLog.game_date)
        .all()
    )
    if len(all_logs) < MIN_TRAIN_SAMPLES:
        logger.info(
            "Insufficient data for dist_%s model: %d rows (need %d)",
            stat_type, len(all_logs), MIN_TRAIN_SAMPLES,
        )
        return []

    quality = _check_training_data_quality(all_logs)
    if not quality['passed']:
        logger.warning(
            "Skipping dist_%s training due to data quality issues: %s",
            stat_type, quality['issues'],
        )
        return []

    if stat_key == 'pra':
        all_logs = wrap_pra_logs(all_logs)

    player_logs: dict = {}
    for log in all_logs:
        player_logs.setdefault(log.player_id, []).append(log)

    team_totals, team_counts = build_team_game_aggregates(all_logs)
    defense_lookup = _build_defense_lookup()
    game_total_lookup = _build_game_total_lookup()

    rows = []
    for pid, logs in player_logs.items():
        logs = sorted(logs, key=lambda lg: ((lg.game_date is None), lg.game_date))
        if len(logs) < 10:
            continue

        for i in range(10, len(logs)):
            prior = logs[:i]
            current = logs[i]
            target = float(getattr(current, stat_key, 0.0) or 0.0)

            team_abbr = (getattr(current, 'team_abbr', '') or '').strip().upper()
            game_total = game_total_lookup.get((current.game_date, team_abbr), 0.0)

            features = build_ml_features_from_history(
                prior_logs=prior,
                current_is_home=(current.home_away or '').lower() == 'home',
                stat_key=stat_key,
                team_totals=team_totals,
                team_counts=team_counts,
                current_game_date=current.game_date,
                current_matchup=current.matchup or '',
                game_total_line=game_total,
                defense_lookup=defense_lookup,
            )
            rows.append((current.game_date, str(pid), features, target))

    rows.sort(key=lambda r: ((r[0] is None), r[0], r[1]))
    return rows


def train_distributional_model(stat_type: str) -> dict:
    """Train a multi-quantile XGBoost head for one continuous stat type.

    Persists a new dist_<stat_type> ModelMetadata row (model_type
    'xgboost_quantile_regressor') via the existing model_storage layer.
    Does not yet fit a calibrator — see Task 7's extension of this function.
    """
    try:
        from xgboost import XGBRegressor
        import numpy as np
    except ImportError:
        logger.error("xgboost not installed")
        return {'error': 'Missing ML dependencies'}

    if stat_type not in DIST_STAT_TYPES:
        return {'error': f'Unsupported distributional stat_type: {stat_type}', 'stat_type': stat_type}

    rows = _build_dist_training_rows(stat_type)
    if not rows:
        return {'error': 'Insufficient training data', 'stat_type': stat_type}

    feature_names = list(rows[0][2].keys())
    X = np.array([[row[2][k] for k in feature_names] for row in rows])
    y = np.array([row[3] for row in rows])

    train_idx, val_idx, split_method, cutoff_date = _date_cutoff_split(rows)
    if not train_idx or not val_idx:
        return {'error': 'Insufficient validation data', 'stat_type': stat_type}

    X_train, X_val = X[train_idx], X[val_idx]
    y_train, y_val = y[train_idx], y[val_idx]

    xgb_params = dict(
        objective='reg:quantileerror',
        quantile_alpha=QUANTILE_ALPHAS,
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        early_stopping_rounds=25,
    )
    model = XGBRegressor(**xgb_params)
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    val_preds_raw = model.predict(X_val)
    val_preds_rectified = [rectify_quantiles(row.tolist()) for row in val_preds_raw]
    val_medians = [median_from_quantiles(QUANTILE_ALPHAS, q) for q in val_preds_rectified]
    val_mae = float(np.mean(np.abs(np.array(val_medians) - y_val)))

    _ensure_model_dir()
    today = date_type.today().isoformat()
    filename = f"dist_{stat_type}_{today}.json"
    filepath = os.path.join(MODEL_DIR, filename)
    model.save_model(filepath)
    artifact_path = persist_model_artifact(filepath, filename)

    try:
        db.session.remove()
        db.engine.dispose()
    except Exception:
        logger.warning("DB pool dispose failed before dist model write", exc_info=True)

    model_name = f"dist_{stat_type}"
    ModelMetadata.query.filter_by(model_name=model_name, is_active=True).update({'is_active': False})
    meta = ModelMetadata(
        model_name=model_name,
        model_type='xgboost_quantile_regressor',
        version=f"{stat_type}_{today}",
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_mae=round(val_mae, 3),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': feature_names,
            'quantile_alphas': QUANTILE_ALPHAS,
            'val_samples': len(X_val),
            'train_samples': len(X_train),
            'split_method': split_method,
            'cutoff_date': cutoff_date.isoformat() if cutoff_date else None,
            'calibrator_model_name': f'dist_calibrator_{stat_type}',
        }),
    )
    db.session.add(meta)
    db.session.commit()

    logger.info(
        "Trained dist_%s model: val_mae=%.3f, %d train / %d val samples",
        stat_type, val_mae, len(X_train), len(X_val),
    )

    return {
        'stat_type': stat_type,
        'val_mae': round(val_mae, 3),
        'train_samples': len(X_train),
        'val_samples': len(X_val),
        'model_path': artifact_path,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_distributional_model -v`
Expected: all tests pass. `test_trains_and_persists_quantile_metadata` and `test_predictions_are_rectifiable_after_save_load` actually train a small XGBoost model (120 rows, 300 estimators, early stopping) — this should take well under a few seconds.

- [ ] **Step 5: Lint**

Run: `ruff check app/services/distributional_model.py tests/test_distributional_model.py`
Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git add app/services/distributional_model.py tests/test_distributional_model.py
git commit -m "feat: train multi-quantile XGBoost heads for points/rebounds/assists/PRA"
```

---

### Task 4: Walk-forward OOF isotonic calibrator (+ reuse reliability tooling)

**Files:**
- Create: `app/services/distribution_calibration.py`
- Modify: `app/services/pick_quality_model.py` (extract `compute_calibration_metrics`, behavior-preserving)
- Test: `tests/test_distribution_calibration.py`
- Test: `tests/test_service_coverage.py` (one new test for the extracted function)

**Interfaces:**
- Consumes: `app.services.distribution.median_from_quantiles`, `prob_over`, `prob_over_poisson` (Task 1/2)
- Produces (used by Tasks 5, 7):
  - `CALIBRATION_LINE_OFFSET_FRACTIONS: Tuple[float, ...]` = `(-0.9, -0.6, -0.3, 0.0, 0.3, 0.6, 0.9)`
  - `collect_oof_pairs_quantile(oof_rows: Sequence[Tuple[Sequence[float], Sequence[float], float]], offset_fractions=...) -> List[Tuple[float, float]]`
  - `collect_oof_pairs_poisson(oof_rows: Sequence[Tuple[float, float]], offset_fractions=...) -> List[Tuple[float, float]]`
  - `fit_isotonic_calibrator(pairs: Sequence[Tuple[float, float]]) -> sklearn.isotonic.IsotonicRegression`
  - `apply_calibrator(calibrator, p_raw: float) -> float`
  - `app.services.pick_quality_model.compute_calibration_metrics(evaluated: list[tuple[float, int]], bins: int = 5) -> dict` — keys: `wins, losses, win_rate, avg_pred, overconfidence_gap, brier, logloss, ece, bins`

**Design note (resolves a spec ambiguity):** the design spec calls for "walk-forward OOF" `(P(over), realized)` pairs, but historical `PlayerGameLog` rows have no associated sportsbook line (real prop lines only exist for the small resolved-`Bet` subset — CLV/lines integration is explicitly out of scope for Increment 1, see Non-Goals). Increment 1 resolves this the same way `ml_model.train_model` already resolves "walk-forward" — one chronological (date-cutoff) holdout is the out-of-fold set — and generates synthetic candidate lines from each held-out row's own point estimate, offset by a *fraction of that row's own quantile spread* (continuous heads) or of λ (Poisson heads), so the offsets scale automatically across stats with very different natural ranges (points vs. blocks) without hardcoding per-stat magnitudes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_distribution_calibration.py`:

```python
"""Tests for app.services.distribution_calibration (Plan C Increment 1)."""

import unittest

ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]


class TestCollectOofPairsQuantile(unittest.TestCase):

    def test_produces_one_pair_per_offset_per_row(self):
        from app.services.distribution_calibration import (
            collect_oof_pairs_quantile, CALIBRATION_LINE_OFFSET_FRACTIONS,
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
        # 22 is above every plausible candidate line built off this row's spread.
        pairs = collect_oof_pairs_quantile([(ALPHAS, rectified, 22.0)])
        self.assertTrue(all(y == 1.0 for _, y in pairs))


class TestCollectOofPairsPoisson(unittest.TestCase):

    def test_produces_one_pair_per_offset_per_row(self):
        from app.services.distribution_calibration import (
            collect_oof_pairs_poisson, CALIBRATION_LINE_OFFSET_FRACTIONS,
        )
        oof_rows = [(2.0, 5.0), (1.0, 0.0)]
        pairs = collect_oof_pairs_poisson(oof_rows)
        self.assertEqual(len(pairs), len(oof_rows) * len(CALIBRATION_LINE_OFFSET_FRACTIONS))
        for p, y in pairs:
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)


class TestFitApplyCalibrator(unittest.TestCase):

    def _miscalibrated_pairs(self):
        # Raw model claims 0.9 confidence for one group and 0.2 for another,
        # but both groups' realized win rate is actually 0.5 — classic
        # overconfidence miscalibration.
        xs = [0.9] * 10 + [0.2] * 10
        ys = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0] + [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        return list(zip(xs, ys))

    def test_fit_raises_on_empty_pairs(self):
        from app.services.distribution_calibration import fit_isotonic_calibrator
        with self.assertRaises(ValueError):
            fit_isotonic_calibrator([])

    def test_fit_and_apply_learns_monotone_correction(self):
        from app.services.distribution_calibration import fit_isotonic_calibrator, apply_calibrator
        calibrator = fit_isotonic_calibrator(self._miscalibrated_pairs())
        self.assertAlmostEqual(apply_calibrator(calibrator, 0.9), 0.5, places=6)
        self.assertAlmostEqual(apply_calibrator(calibrator, 0.2), 0.5, places=6)

    def test_apply_calibrator_clamped_to_unit_interval(self):
        from app.services.distribution_calibration import fit_isotonic_calibrator, apply_calibrator
        calibrator = fit_isotonic_calibrator(self._miscalibrated_pairs())
        p = apply_calibrator(calibrator, 5.0)  # out-of-range raw input
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_calibration_improves_ece_on_miscalibrated_fixture(self):
        from app.services.distribution_calibration import fit_isotonic_calibrator, apply_calibrator
        from app.services.pick_quality_model import compute_calibration_metrics

        pairs = self._miscalibrated_pairs()
        before = compute_calibration_metrics(pairs, bins=5)
        self.assertAlmostEqual(before['ece'], 0.35, places=4)

        calibrator = fit_isotonic_calibrator(pairs)
        calibrated_pairs = [(apply_calibrator(calibrator, p), y) for p, y in pairs]
        after = compute_calibration_metrics(calibrated_pairs, bins=5)
        self.assertAlmostEqual(after['ece'], 0.0, places=4)
        self.assertLess(after['ece'], before['ece'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `SECRET_KEY=test python -m unittest tests.test_distribution_calibration -v`
Expected: `ModuleNotFoundError: No module named 'app.services.distribution_calibration'`, and (once that's fixed in isolation) `AttributeError: ... has no attribute 'compute_calibration_metrics'` from `pick_quality_model`.

- [ ] **Step 3a: Extract `compute_calibration_metrics` from `pick_quality_model.get_calibration_report` (behavior-preserving refactor)**

In `app/services/pick_quality_model.py`, insert this new function directly above `def get_calibration_report(`:

```python
def compute_calibration_metrics(evaluated: list[tuple[float, int]], bins: int = 5) -> dict:
    """Brier score, log-loss, ECE, and per-bin reliability stats for (p, y) pairs.

    ``evaluated`` is a list of (predicted_probability, realized_outcome in
    {0, 1}) tuples. Shared by pick-quality Model 2 calibration reporting
    (get_calibration_report, below) and the Plan C distributional-model
    backtest gate (app/services/distributional_model.py backtest_verdict).
    """
    n = len(evaluated)
    wins = sum(y for _, y in evaluated)
    losses = n - wins
    avg_pred = sum(p for p, _ in evaluated) / n
    win_rate = wins / n

    brier = sum((p - y) ** 2 for p, y in evaluated) / n
    logloss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in evaluated) / n

    bin_rows = []
    ece_weighted_sum = 0.0
    for idx in range(bins):
        start = idx / bins
        end = (idx + 1) / bins
        values = [(p, y) for p, y in evaluated if (start <= p < end) or (idx == bins - 1 and p == 1.0)]
        if not values:
            bin_rows.append({
                'range': f'{start:.2f}-{end:.2f}',
                'count': 0,
                'avg_pred': None,
                'win_rate': None,
                'gap': None,
            })
            continue

        b_count = len(values)
        b_avg = sum(p for p, _ in values) / b_count
        b_win = sum(y for _, y in values) / b_count
        gap = b_avg - b_win
        bin_rows.append({
            'range': f'{start:.2f}-{end:.2f}',
            'count': b_count,
            'avg_pred': round(b_avg, 3),
            'win_rate': round(b_win, 3),
            'gap': round(gap, 3),
        })
        ece_weighted_sum += (b_count / n) * abs(gap)

    return {
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 3),
        'avg_pred': round(avg_pred, 3),
        'overconfidence_gap': round(avg_pred - win_rate, 3),
        'brier': round(brier, 4),
        'logloss': round(logloss, 4),
        'ece': round(ece_weighted_sum, 4),
        'bins': bin_rows,
    }
```

Then replace the body of `get_calibration_report` from `n = len(evaluated)` through its final `return {...}` (currently `pick_quality_model.py:680-729`) with:

```python
    metrics = compute_calibration_metrics(evaluated, bins=bins)
    return {
        'model_version': model_version,
        'total_rows': len(rows),
        'evaluated': n,
        'no_model_count': no_model_count,
        'recommendation_counts': recommendation_counts,
        **metrics,
    }
```

This preserves every existing key (`model_version`, `total_rows`, `evaluated`, `no_model_count`, `wins`, `losses`, `win_rate`, `avg_pred`, `overconfidence_gap`, `brier`, `logloss`, `recommendation_counts`, `bins`) with identical values, and adds one new key: `ece`.

- [ ] **Step 3b: Add a characterization test for the extraction**

Add to `tests/test_service_coverage.py` (in the same test class as the existing `test_get_calibration_report_no_picks`, or as a standalone test function — either is fine since `compute_calibration_metrics` takes no DB/app-context dependency):

```python
    def test_compute_calibration_metrics_matches_manual_calculation(self):
        from app.services.pick_quality_model import compute_calibration_metrics
        xs = [0.9] * 10 + [0.2] * 10
        ys = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0] + [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        result = compute_calibration_metrics(list(zip(xs, ys)), bins=5)
        self.assertEqual(result['wins'], 10)
        self.assertEqual(result['losses'], 10)
        self.assertAlmostEqual(result['win_rate'], 0.5)
        self.assertAlmostEqual(result['avg_pred'], 0.55)
        self.assertAlmostEqual(result['ece'], 0.35, places=4)
```

- [ ] **Step 4: Run tests to verify Step 3 passes and nothing regressed**

Run: `SECRET_KEY=test python -m unittest tests.test_service_coverage -v`
Expected: `test_compute_calibration_metrics_matches_manual_calculation` passes; `test_get_calibration_report_no_picks` and `test_get_calibration_report_invalid_params` (pre-existing) still pass unchanged.

- [ ] **Step 5: Write `app/services/distribution_calibration.py`**

```python
"""Walk-forward OOF isotonic calibration for distributional P(over) heads.

Fits/applies an isotonic calibrator on pooled (raw model P(over), realized
over-or-under) pairs. The OOF pairs themselves come from either a
quantile-head holdout (collect_oof_pairs_quantile) or a Poisson-head
holdout (collect_oof_pairs_poisson) — both generate synthetic candidate
lines around each held-out row's own point estimate, since historical
PlayerGameLog rows have no associated sportsbook line (see Task 4's design
note in the plan for why).
"""

from typing import List, Sequence, Tuple

from sklearn.isotonic import IsotonicRegression

from app.services.distribution import median_from_quantiles, prob_over, prob_over_poisson

# Offsets are fractions of each row's own natural spread (quantile
# half-range for continuous heads, lambda for Poisson heads) rather than
# fixed absolute values, so the same offsets work whether the stat's scale
# is ~0-50 (points) or ~0-5 (blocks).
CALIBRATION_LINE_OFFSET_FRACTIONS: Tuple[float, ...] = (-0.9, -0.6, -0.3, 0.0, 0.3, 0.6, 0.9)


def collect_oof_pairs_quantile(
    oof_rows: Sequence[Tuple[Sequence[float], Sequence[float], float]],
    offset_fractions: Sequence[float] = CALIBRATION_LINE_OFFSET_FRACTIONS,
) -> List[Tuple[float, float]]:
    """Build (raw P(over), realized_over) pairs from quantile-head OOF rows.

    Each ``oof_rows`` entry is
    ``(alphas, rectified_quantile_values, realized_value)`` for one held-out
    row. Candidate lines are the row's own median +/- (fraction * half the
    quantile spread), giving genuinely different points on the CDF than the
    training knots (so calibration has real work to do).
    """
    pairs: List[Tuple[float, float]] = []
    for alphas, qvals, realized in oof_rows:
        median = median_from_quantiles(alphas, qvals)
        half_spread = max((qvals[-1] - qvals[0]) / 2.0, 0.5)
        for frac in offset_fractions:
            line = median + frac * half_spread
            p_raw = prob_over(line, alphas, qvals)
            y = 1.0 if realized > line else 0.0
            pairs.append((p_raw, y))
    return pairs


def collect_oof_pairs_poisson(
    oof_rows: Sequence[Tuple[float, float]],
    offset_fractions: Sequence[float] = CALIBRATION_LINE_OFFSET_FRACTIONS,
) -> List[Tuple[float, float]]:
    """Build (raw P(over), realized_over) pairs from Poisson-head OOF rows.

    Each ``oof_rows`` entry is ``(lam, realized_value)`` for one held-out
    row. Candidate lines are half-integers near lam (matching real prop
    convention) scaled by the same offset fractions used for quantile heads.
    """
    import math

    pairs: List[Tuple[float, float]] = []
    for lam, realized in oof_rows:
        half_spread = max(lam, 1.0)
        for frac in offset_fractions:
            candidate = lam + frac * half_spread
            line = max(0.5, math.floor(candidate) + 0.5)
            p_raw = prob_over_poisson(line, lam)
            y = 1.0 if realized > line else 0.0
            pairs.append((p_raw, y))
    return pairs


def fit_isotonic_calibrator(pairs: Sequence[Tuple[float, float]]) -> IsotonicRegression:
    """Fit an isotonic regression mapping raw P(over) -> calibrated P(over).

    Raises ValueError if ``pairs`` is empty (nothing to fit on).
    """
    if not pairs:
        raise ValueError("Cannot fit a calibrator on an empty pair set")
    xs = [p for p, _ in pairs]
    ys = [y for _, y in pairs]
    calibrator = IsotonicRegression(out_of_bounds='clip', y_min=0.0, y_max=1.0)
    calibrator.fit(xs, ys)
    return calibrator


def apply_calibrator(calibrator: IsotonicRegression, p_raw: float) -> float:
    """Apply a fitted calibrator to a single raw probability, clamped to [0, 1]."""
    calibrated = float(calibrator.predict([p_raw])[0])
    return min(max(calibrated, 0.0), 1.0)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_distribution_calibration -v`
Expected: all tests pass, including `test_calibration_improves_ece_on_miscalibrated_fixture` (`before['ece'] == 0.35`, `after['ece'] == 0.0`).

- [ ] **Step 7: Lint**

Run: `ruff check app/services/distribution_calibration.py app/services/pick_quality_model.py tests/test_distribution_calibration.py tests/test_service_coverage.py`
Expected: no issues.

- [ ] **Step 8: Commit**

```bash
git add app/services/distribution_calibration.py app/services/pick_quality_model.py \
        tests/test_distribution_calibration.py tests/test_service_coverage.py
git commit -m "feat: add walk-forward OOF isotonic calibration utilities"
```

---

### Task 5: Distributional predictor service (unified inference entry point)

**Files:**
- Create: `app/services/distributional_predictor.py`
- Test: `tests/test_distributional_predictor.py`

**Interfaces:**
- Consumes:
  - `app.services.distributional_model.DIST_STAT_TYPES`, `POISSON_DIST_STAT_TYPES`, `QUANTILE_ALPHAS`, `_build_dist_training_rows` (Task 3; test-only)
  - `app.services.distribution.median_from_quantiles`, `prob_over`, `prob_over_poisson`, `rectify_quantiles` (Task 1/2)
  - `app.services.distribution_calibration.apply_calibrator` (Task 4)
  - `app.services.model_storage.materialize_model_artifact` (existing)
  - `app.services.ml_model.predict_stat(stat_type, features) -> float`, `load_active_model(stat_type)` (existing, for Poisson heads)
  - `app.models.ModelMetadata` — rows named `dist_<stat_type>` (quantile) and `dist_calibrator_<stat_type>` (both head kinds)
- Produces (used by Task 6, Task 7's backtest):
  - `load_quantile_model(stat_type: str) -> tuple[XGBRegressor|None, list[str]|None]`
  - `load_calibrator(stat_type: str) -> IsotonicRegression|None`
  - `predict_distribution(stat_type: str, features: dict) -> dict|None` — dict keys: `kind` (`'quantile'`|`'poisson'`), `point`, plus `alphas`/`quantile_values` (quantile) or `lam` (poisson)
  - `predict_prob_over(stat_type: str, features: dict, line: float) -> float|None` — `None` means "no distributional model available, caller should fall back"

- [ ] **Step 1: Write the failing test**

Create `tests/test_distributional_predictor.py`:

```python
"""Tests for the unified distributional predictor service (Task 5)."""

import os
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
    for pid in ('701', '702', '703'):
        _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
    with patch.object(dm, 'MIN_TRAIN_SAMPLES', 50):
        dm.train_distributional_model('player_points')


class TestPredictDistribution(BaseTestCase):

    def test_no_model_returns_none(self):
        from app.services.distributional_predictor import predict_distribution
        with self.app.app_context():
            result = predict_distribution('player_points', {'avg_stat_last_5': 20.0})
        self.assertIsNone(result)

    def test_unsupported_stat_type_returns_none(self):
        from app.services.distributional_predictor import predict_distribution
        with self.app.app_context():
            result = predict_distribution('player_assist_to_turnover_ratio', {})
        self.assertIsNone(result)

    def test_quantile_head_point_matches_interpolated_median(self):
        from app.services import distributional_model as dm
        from app.services.distributional_predictor import predict_distribution
        from app.services.distribution import median_from_quantiles

        with self.app.app_context():
            _train_points_model()
            rows = dm._build_dist_training_rows('player_points')
            _, _, features, _ = rows[-1]
            dist = predict_distribution('player_points', features)

        self.assertIsNotNone(dist)
        self.assertEqual(dist['kind'], 'quantile')
        self.assertEqual(dist['quantile_values'], sorted(dist['quantile_values']))
        self.assertAlmostEqual(
            dist['point'], median_from_quantiles(dist['alphas'], dist['quantile_values']),
        )

    def test_poisson_head_uses_existing_point_model(self):
        from app.services import ml_model
        from app.services.distributional_predictor import predict_distribution

        with self.app.app_context():
            for pid in ('801', '802', '803'):
                _seed_dist_logs(player_id=pid, count=40, seed_offset=int(pid))
            with patch.object(ml_model, 'MIN_TRAIN_SAMPLES', 50):
                ml_model.train_model('player_steals')
            rows = ml_model._build_training_rows('player_steals')
            _, _, features, _ = rows[-1]
            dist = predict_distribution('player_steals', features)

        self.assertIsNotNone(dist)
        self.assertEqual(dist['kind'], 'poisson')
        self.assertGreater(dist['lam'], 0)
        self.assertEqual(dist['point'], dist['lam'])


class TestPredictProbOver(BaseTestCase):

    def test_no_model_returns_none(self):
        from app.services.distributional_predictor import predict_prob_over
        with self.app.app_context():
            result = predict_prob_over('player_points', {'avg_stat_last_5': 20.0}, 20.5)
        self.assertIsNone(result)

    def test_monotone_non_increasing_in_line(self):
        from app.services import distributional_model as dm
        from app.services.distributional_predictor import predict_prob_over

        with self.app.app_context():
            _train_points_model()
            rows = dm._build_dist_training_rows('player_points')
            _, _, features, _ = rows[-1]
            probs = [predict_prob_over('player_points', features, line) for line in (5, 15, 25, 35, 45)]

        self.assertTrue(all(p is not None for p in probs))
        self.assertEqual(probs, sorted(probs, reverse=True))
        self.assertTrue(all(0.0 <= p <= 1.0 for p in probs))

    def test_applies_calibrator_when_one_is_active(self):
        from app.services import distributional_model as dm
        from app.services.distributional_predictor import predict_prob_over
        from app.services.model_storage import persist_model_artifact

        with self.app.app_context():
            _train_points_model()
            rows = dm._build_dist_training_rows('player_points')
            _, _, features, _ = rows[-1]

            # A deliberately squashing calibrator: every raw P(over) -> 0.5.
            calibrator = IsotonicRegression(out_of_bounds='clip', y_min=0.0, y_max=1.0)
            calibrator.fit([0.0, 1.0], [0.5, 0.5])
            filepath = os.path.join(tempfile.gettempdir(), 'dist_calibrator_player_points_test.pkl')
            joblib.dump(calibrator, filepath)
            artifact_path = persist_model_artifact(filepath, 'dist_calibrator_player_points_test.pkl')

            db.session.add(ModelMetadata(
                model_name='dist_calibrator_player_points',
                model_type='isotonic_calibrator',
                version='test',
                file_path=artifact_path,
                training_date=datetime.now(timezone.utc),
                is_active=True,
            ))
            db.session.commit()

            calibrated = predict_prob_over('player_points', features, 20.5)

        self.assertAlmostEqual(calibrated, 0.5, places=6)


if __name__ == '__main__':
    import unittest
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `SECRET_KEY=test python -m unittest tests.test_distributional_predictor -v`
Expected: `ModuleNotFoundError: No module named 'app.services.distributional_predictor'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/services/distributional_predictor.py`:

```python
"""Unified distributional inference: quantile heads (points/rebounds/
assists/PRA) and Poisson heads (threes/steals/blocks), each behind its own
isotonic calibrator. Single entry point ValueDetector consumes for the
calibrated P(over) — see Task 6.
"""

import json
import logging
from typing import Optional

from app.models import ModelMetadata
from app.services.distribution import (
    median_from_quantiles,
    prob_over,
    prob_over_poisson,
    rectify_quantiles,
)
from app.services.distribution_calibration import apply_calibrator
from app.services.distributional_model import DIST_STAT_TYPES, POISSON_DIST_STAT_TYPES, QUANTILE_ALPHAS
from app.services.model_storage import materialize_model_artifact

logger = logging.getLogger(__name__)


def load_quantile_model(stat_type: str):
    """Return (model, feature_names) for the active dist_<stat_type> model.

    Returns (None, None) if no active model or artifact is available.
    """
    from xgboost import XGBRegressor

    meta = ModelMetadata.query.filter_by(model_name=f"dist_{stat_type}", is_active=True).first()
    if not meta:
        return None, None
    local_path = materialize_model_artifact(meta.file_path)
    if not local_path:
        return None, None

    model = XGBRegressor()
    model.load_model(local_path)

    feature_names = None
    if meta.metadata_json:
        try:
            feature_names = json.loads(meta.metadata_json).get('feature_names')
        except (ValueError, TypeError):
            pass
    return model, feature_names


def load_calibrator(stat_type: str):
    """Return the active dist_calibrator_<stat_type> IsotonicRegression, or None."""
    meta = ModelMetadata.query.filter_by(model_name=f"dist_calibrator_{stat_type}", is_active=True).first()
    if not meta:
        return None
    local_path = materialize_model_artifact(meta.file_path)
    if not local_path:
        return None
    try:
        import joblib
        return joblib.load(local_path)
    except Exception as exc:
        logger.warning("Failed to load calibrator for %s: %s", stat_type, exc)
        return None


def predict_distribution(stat_type: str, features: dict) -> Optional[dict]:
    """Predict a raw (uncalibrated) distribution for one (stat_type, features) row.

    Returns None if no distributional model is available for stat_type —
    the caller should fall back to the synthetic Gaussian.
    """
    import numpy as np

    if stat_type in DIST_STAT_TYPES:
        model, feature_names = load_quantile_model(stat_type)
        if model is None or feature_names is None:
            return None
        missing = [k for k in feature_names if k not in features]
        if missing:
            logger.warning("Missing dist features for %s — zero-filled: %s", stat_type, missing)
        X = np.array([[features.get(k, 0) for k in feature_names]])
        raw = model.predict(X)[0].tolist()
        rectified = rectify_quantiles(raw)
        point = median_from_quantiles(QUANTILE_ALPHAS, rectified)
        return {
            'kind': 'quantile',
            'point': point,
            'alphas': QUANTILE_ALPHAS,
            'quantile_values': rectified,
        }

    if stat_type in POISSON_DIST_STAT_TYPES:
        from app.services.ml_model import predict_stat

        lam = predict_stat(stat_type, features)
        if lam <= 0:
            return None
        return {'kind': 'poisson', 'point': lam, 'lam': lam}

    return None


def predict_prob_over(stat_type: str, features: dict, line: float) -> Optional[float]:
    """Calibrated P(stat > line).

    Returns None when no distributional model is active for stat_type —
    the caller (ValueDetector) falls back to the synthetic Gaussian.
    """
    dist = predict_distribution(stat_type, features)
    if dist is None:
        return None

    if dist['kind'] == 'poisson':
        raw = prob_over_poisson(line, dist['lam'])
    else:
        raw = prob_over(line, dist['alphas'], dist['quantile_values'])

    calibrator = load_calibrator(stat_type)
    if calibrator is not None:
        return apply_calibrator(calibrator, raw)
    return float(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_distributional_predictor -v`
Expected: all tests pass.

- [ ] **Step 5: Lint**

Run: `ruff check app/services/distributional_predictor.py tests/test_distributional_predictor.py`
Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git add app/services/distributional_predictor.py tests/test_distributional_predictor.py
git commit -m "feat: add unified distributional predictor (quantile + Poisson heads)"
```

---

### Task 6: ValueDetector integration behind `USE_DISTRIBUTIONAL_MODEL`

**Files:**
- Modify: `app/services/value_detector.py`
- Test: `tests/test_services.py` (append to the existing `TestValueDetector` class)

**Interfaces:**
- Consumes:
  - `app.services.distributional_model.DIST_STAT_KEY_MAP`, `wrap_pra_logs` (Task 3)
  - `app.services.distributional_predictor.predict_prob_over(stat_type, features, line) -> float|None` (Task 5)
  - `app.services.ml_model._build_defense_lookup() -> dict` (existing)
  - `ProjectionEngine._player_state_cache`, `ProjectionEngine._context_cache`, `ProjectionEngine._build_ml_features(...)` (existing instance state/method, `app/services/projection_engine.py:91-97, 438-475`) — reused as-is, no changes to `ProjectionEngine`.
  - `app.config_display.PROP_STAT_KEY` (existing; not currently imported in `value_detector.py`)
- Produces (no new public API; behavioral contract only):
  - `ValueDetector._model_prob_over(self, projection, line, std_dev, player_name='', prop_type='', opponent_name='', team_name='', is_home=True, game_date=None) -> float` — the 3 new-callers-only kwargs are additive; `detector._model_prob_over(30, 25, 5)` (the existing test's exact call) is unaffected.
  - `ValueDetector._use_distributional_model(self) -> bool`
  - `ValueDetector._build_dist_features(self, player_name, prop_type, opponent_name, team_name, is_home, game_date) -> tuple[str|None, dict|None]`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_services.py`, inside the existing `class TestValueDetector(BaseTestCase):` (immediately after the existing `test_model_prob_over_scipy` method, before `# -- _empty_score --`):

```python
    def test_model_prob_over_flag_off_ignores_context_kwargs(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        legacy = detector._model_prob_over(30, 25, 5)
        with_context = detector._model_prob_over(
            30, 25, 5, player_name='Anyone', prop_type='player_points',
        )
        self.assertEqual(legacy, with_context)

    def test_model_prob_over_uses_distributional_predictor_when_flag_on(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id='910', player_name='Dist Flag Player', team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25 + (i % 3), reb=6, ast=4, fg3m=2, minutes=33,
                    stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
                ))
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='910'), \
                 patch.dict('os.environ', {'USE_DISTRIBUTIONAL_MODEL': 'true'}), \
                 patch('app.services.distributional_predictor.predict_prob_over', return_value=0.777):
                result = detector.score_prop(
                    'Dist Flag Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertAlmostEqual(result['model_prob_over'], 0.777)

    def test_model_prob_over_falls_back_when_predictor_returns_none(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id='911', player_name='Fallback Player', team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25, reb=6, ast=4, fg3m=2, minutes=33,
                    stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
                ))
            db.session.commit()

            detector_on = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='911'), \
                 patch.dict('os.environ', {'USE_DISTRIBUTIONAL_MODEL': 'true'}), \
                 patch('app.services.distributional_predictor.predict_prob_over', return_value=None):
                flag_on_result = detector_on.score_prop(
                    'Fallback Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )

            detector_off = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='911'):
                flag_off_result = detector_off.score_prop(
                    'Fallback Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertAlmostEqual(flag_on_result['model_prob_over'], flag_off_result['model_prob_over'])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `SECRET_KEY=test python -m unittest tests.test_services -v -k TestValueDetector`
Expected: `test_model_prob_over_flag_off_ignores_context_kwargs` fails with `TypeError: _model_prob_over() got an unexpected keyword argument 'player_name'`; the other two new tests fail the same way (or with `ModuleNotFoundError` once patch targets an as-yet-nonexistent import path — the `TypeError` on kwargs surfaces first since it's raised before `patch()` context bodies execute their assertions).

- [ ] **Step 3: Write minimal implementation**

In `app/services/value_detector.py`, add `import os` to the top-of-file imports (alongside the existing `import json`, `import logging`, `import math`) and add `from app.config_display import PROP_STAT_KEY`:

```python
import json
import logging
import math
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, datetime
from itertools import combinations
from typing import Optional

from app.config_display import PROP_STAT_KEY
from app.models import PlayerGameLog
from app.services.pick_quality_model import predict_pick_quality
from app.services.projection_engine import ProjectionEngine
from app.services.feature_engine import build_pick_context_features
from app.utils.time_helpers import ET
from app.services.context_service import is_player_available
from app.services.stats_service import find_player_id, get_cached_logs
from app.utils.odds import american_from_decimal, decimal_odds, implied_prob
from app.utils.time_helpers import et_date_str
```

Replace the call site in `score_prop` (currently `app/services/value_detector.py:164-165`):

```python
        # Model probability of exceeding the line (normal CDF approximation)
        model_prob_over = self._model_prob_over(projection, line, std_dev)
```

with:

```python
        # Model probability of exceeding the line (calibrated distributional
        # model when USE_DISTRIBUTIONAL_MODEL=true; normal CDF approximation
        # otherwise — see _model_prob_over).
        model_prob_over = self._model_prob_over(
            projection, line, std_dev,
            player_name=player_name, prop_type=prop_type,
            opponent_name=opponent_name, team_name=team_name,
            is_home=is_home, game_date=game_date,
        )
```

Replace `_model_prob_over` (currently `app/services/value_detector.py:271-286`) with:

```python
    def _model_prob_over(
        self,
        projection: float,
        line: float,
        std_dev: float,
        player_name: str = '',
        prop_type: str = '',
        opponent_name: str = '',
        team_name: str = '',
        is_home: bool = True,
        game_date: Optional[_date] = None,
    ) -> float:
        """Estimate probability of the player exceeding the line.

        When USE_DISTRIBUTIONAL_MODEL=true and a trained distributional
        model is available for (player_name, prop_type), P(over) comes from
        the calibrated model CDF (quantile heads for points/rebounds/
        assists/PRA, Poisson CDF for threes/steals/blocks). Otherwise (flag
        off, or no model/features available) falls back to the legacy
        Normal(projection, std_dev) synthetic CDF — byte-identical to
        pre-Plan-C behavior.
        """
        if self._use_distributional_model() and player_name and prop_type:
            try:
                stat_type, features = self._build_dist_features(
                    player_name, prop_type, opponent_name, team_name, is_home, game_date,
                )
                if features:
                    from app.services.distributional_predictor import predict_prob_over
                    calibrated = predict_prob_over(stat_type, features, line)
                    if calibrated is not None:
                        return calibrated
            except Exception as exc:
                logger.warning(
                    "Distributional P(over) failed for %s/%s; falling back to Gaussian: %s",
                    player_name, prop_type, exc,
                )

        if std_dev <= 0:
            return 0.65 if projection > line else 0.35

        try:
            from scipy.stats import norm
            return float(1.0 - norm.cdf(line, loc=projection, scale=std_dev))
        except ImportError:
            # Fallback: approximate normal CDF using the error function
            z = (line - projection) / std_dev
            return 0.5 * (1.0 + math.erf(-z / math.sqrt(2)))

    def _use_distributional_model(self) -> bool:
        return os.getenv('USE_DISTRIBUTIONAL_MODEL', 'false').lower() == 'true'

    def _build_dist_features(
        self,
        player_name: str,
        prop_type: str,
        opponent_name: str,
        team_name: str,
        is_home: bool,
        game_date: Optional[_date],
    ):
        """Build the 30-key ML feature dict for the distributional predictor.

        Reuses ProjectionEngine's already-cached (player_id, logs, summary)
        from the project_stat() call score_prop() just made — no extra DB
        round trip. Returns (prop_type, features) or (None, None) when
        unavailable (caller falls back to the synthetic Gaussian).
        """
        from app.services.distributional_model import DIST_STAT_KEY_MAP, wrap_pra_logs
        from app.services.ml_model import _build_defense_lookup

        player_cache_key = str(player_name).strip().lower()
        state = self.engine._player_state_cache.get(player_cache_key)
        if not state:
            return None, None
        _, logs, _ = state
        if len(logs) < 10:
            return None, None

        stat_key = DIST_STAT_KEY_MAP.get(prop_type)
        use_logs = logs
        if stat_key:
            if stat_key == 'pra':
                use_logs = wrap_pra_logs(logs)
        else:
            stat_key = PROP_STAT_KEY.get(prop_type)
            if not stat_key:
                return None, None

        defense_cache_key = '__dist_defense_lookup__'
        defense_lookup = self.engine._context_cache.get(defense_cache_key)
        if defense_lookup is None:
            try:
                defense_lookup = _build_defense_lookup()
            except Exception:
                defense_lookup = {}
            self.engine._context_cache[defense_cache_key] = defense_lookup

        current_matchup = ''
        if team_name and opponent_name:
            sep = ' vs. ' if is_home else ' @ '
            current_matchup = f"{team_name}{sep}{opponent_name}"

        features = self.engine._build_ml_features(
            use_logs, stat_key, is_home,
            current_matchup=current_matchup,
            defense_lookup=defense_lookup,
            game_date=game_date,
        )
        return prop_type, features
```

- [ ] **Step 4: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_services -v -k TestValueDetector`
Expected: all `TestValueDetector` tests pass, including the 3 new ones and every pre-existing one (`test_score_prop_full_with_edge`, `test_score_prop_zero_std`, `test_score_prop_under_recommended`, `test_model_prob_over_scipy`, etc.) — flag defaults to `false` so none of those are affected.

- [ ] **Step 5: Run the full services test module (regression check)**

Run: `SECRET_KEY=test python -m unittest tests.test_services -v`
Expected: all tests pass (this module is large — confirms the `value_detector.py` import changes don't break anything else that imports from it).

- [ ] **Step 6: Lint**

Run: `ruff check app/services/value_detector.py tests/test_services.py`
Expected: no issues.

- [ ] **Step 7: Commit**

```bash
git add app/services/value_detector.py tests/test_services.py
git commit -m "feat: wire calibrated distributional P(over) into ValueDetector behind USE_DISTRIBUTIONAL_MODEL"
```

---

### Task 7: retrain CLI extension + backtest gate

**Files:**
- Modify: `app/services/distributional_model.py` (add calibrator fitting + Poisson-head calibration + retrain-all + backtest-verdict helper)
- Modify: `app/cli/model_commands.py` (extend `cli_retrain`; add `cli_backtest`; register it)
- Test: `tests/test_distributional_model.py` (append)
- Test: `tests/test_services.py` (append to `TestModelCommandsStatus`)

**Interfaces:**
- Consumes: `app.services.distribution_calibration.collect_oof_pairs_quantile`, `collect_oof_pairs_poisson`, `fit_isotonic_calibrator` (Task 4); `app.services.pick_quality_model.compute_calibration_metrics` (Task 4); `app.services.distributional_predictor.load_quantile_model`, `load_calibrator` (Task 5); `app.services.ml_model.load_active_model`, `_build_training_rows` (existing)
- Produces (used by the CLI, and available for a later Phase 2 dashboard):
  - `train_distributional_model(stat_type)` (Task 3) — extended to also fit+persist a `dist_calibrator_<stat_type>` row
  - `_collect_poisson_oof_rows(stat_type: str, frac: float = 0.8) -> list[tuple[float, float]]`
  - `train_distributional_calibrator_for_poisson_stat(stat_type: str) -> dict`
  - `retrain_all_distributional_models() -> dict` — `{stat_type: result_dict, ...}` for all 7 stat types
  - `backtest_verdict(dist_ece: float, gauss_ece: float, gate: float = 0.03) -> str` — `'PROMOTE'` or `'HOLD'`
  - CLI: `flask retrain --force` also retrains distributional heads; new `flask backtest --stat-type <stat>`

- [ ] **Step 1: Write the failing test (calibrator now persisted by `train_distributional_model`)**

Append to `tests/test_distributional_model.py`:

```python
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
```

Also add, to `tests/test_services.py`'s `TestModelCommandsStatus` class (after `test_cli_retrain_force`):

```python
    @patch('app.services.distributional_model.retrain_all_distributional_models')
    @patch('app.services.ml_model.retrain_all_models')
    @patch('app.services.pick_quality_model.train_pick_quality_model')
    @patch('app.services.market_recommender.train_market_models')
    def test_cli_retrain_force_also_trains_distributional_heads(
        self, mock_market, mock_pq, mock_retrain, mock_dist,
    ):
        """cli_retrain --force also retrains the Plan C distributional heads."""
        mock_retrain.return_value = {'player_points': {'ok': True}}
        mock_pq.return_value = {'status': 'ok'}
        mock_market.return_value = {'status': 'ok'}
        mock_dist.return_value = {'player_points': {'ok': True}}
        from app.cli.model_commands import cli_retrain
        with self.app.app_context():
            result = self._invoke(cli_retrain, ['--force'])
        self.assertEqual(result.exit_code, 0)
        mock_dist.assert_called_once()
        self.assertIn('Distributional retrain', result.output)

    def test_backtest_cli_no_active_model(self):
        """flask backtest exits cleanly when no dist_<stat> model exists yet."""
        from app.cli.model_commands import cli_backtest
        with self.app.app_context():
            result = self._invoke(cli_backtest, ['--stat-type', 'player_points'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('No active dist_player_points model', result.output)

    def test_backtest_cli_unsupported_stat_type(self):
        from app.cli.model_commands import cli_backtest
        with self.app.app_context():
            result = self._invoke(cli_backtest, ['--stat-type', 'player_rebounds_per_minute'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Unsupported stat_type', result.output)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SECRET_KEY=test python -m unittest tests.test_distributional_model -v`
Expected: `TestTrainDistributionalModelWithCalibrator` fails with `KeyError: 'calibrator_fitted'`; `TestPoissonOofCalibration` and `TestRetrainAllDistributionalModels` fail with `AttributeError` (functions don't exist yet); `TestBacktestVerdict` fails with `ImportError`.

Run: `SECRET_KEY=test python -m unittest tests.test_services -v -k TestModelCommandsStatus`
Expected: `test_cli_retrain_force_also_trains_distributional_heads` fails (`mock_dist.assert_called_once()` — never called); `test_backtest_cli_*` fail with `ImportError: cannot import name 'cli_backtest'`.

- [ ] **Step 3a: Extend `train_distributional_model` to fit + persist a calibrator**

In `app/services/distributional_model.py`, add to the top-of-file imports:

```python
from app.services.distribution_calibration import collect_oof_pairs_quantile, fit_isotonic_calibrator
```

In `train_distributional_model`, insert the following **between** the existing `val_mae = float(np.mean(...))` line and the `_ensure_model_dir()` line:

```python
    calibration_pairs = collect_oof_pairs_quantile(
        list(zip([QUANTILE_ALPHAS] * len(val_preds_rectified), val_preds_rectified, y_val.tolist())),
    )
```

Then, **after** the existing `db.session.add(meta)` / `db.session.commit()` block for the quantile model, add:

```python
    calibrator_fitted = False
    calibrator_model_name = f'dist_calibrator_{stat_type}'
    try:
        calibrator = fit_isotonic_calibrator(calibration_pairs)
        import joblib
        calibrator_filename = f"{calibrator_model_name}_{today}.pkl"
        calibrator_filepath = os.path.join(MODEL_DIR, calibrator_filename)
        joblib.dump(calibrator, calibrator_filepath)
        calibrator_artifact_path = persist_model_artifact(calibrator_filepath, calibrator_filename)

        ModelMetadata.query.filter_by(model_name=calibrator_model_name, is_active=True).update({'is_active': False})
        db.session.add(ModelMetadata(
            model_name=calibrator_model_name,
            model_type='isotonic_calibrator',
            version=f"{stat_type}_{today}",
            file_path=calibrator_artifact_path,
            training_date=datetime.now(timezone.utc),
            training_samples=len(calibration_pairs),
            is_active=True,
            metadata_json=json.dumps({'oof_pairs': len(calibration_pairs)}),
        ))
        db.session.commit()
        calibrator_fitted = True
    except ValueError:
        logger.warning("No OOF calibration pairs for dist_%s; skipping calibrator fit", stat_type)
```

And update the function's final `return` statement to:

```python
    return {
        'stat_type': stat_type,
        'val_mae': round(val_mae, 3),
        'train_samples': len(X_train),
        'val_samples': len(X_val),
        'model_path': artifact_path,
        'calibrator_fitted': calibrator_fitted,
        'calibration_pairs': len(calibration_pairs),
    }
```

- [ ] **Step 3b: Add Poisson-head OOF collection + calibrator training**

Append to `app/services/distributional_model.py`:

```python
def _collect_poisson_oof_rows(stat_type: str, frac: float = 0.8) -> list:
    """Rebuild the existing point model's training rows, re-derive the same
    chronological holdout split, and predict lambda for each held-out row
    using the ALREADY-ACTIVE point regressor. Returns a list of
    (lam, realized_value) pairs, or [] if no active model/rows exist.
    """
    from app.services.ml_model import _build_training_rows as _build_point_training_rows
    from app.services.ml_model import load_active_model

    model, feature_names = load_active_model(stat_type)
    if model is None or feature_names is None:
        return []

    rows = _build_point_training_rows(stat_type)
    if not rows:
        return []

    _, val_idx, _, _ = _date_cutoff_split(rows, frac=frac)
    if not val_idx:
        return []

    import numpy as np
    oof_rows = []
    for idx in val_idx:
        _, _, features, target = rows[idx]
        X = np.array([[features.get(k, 0) for k in feature_names]])
        lam = float(model.predict(X)[0])
        if lam > 0:
            oof_rows.append((lam, target))
    return oof_rows


def train_distributional_calibrator_for_poisson_stat(stat_type: str) -> dict:
    """Fit + persist an isotonic calibrator for an existing Poisson point model.

    Increment 1 reuses the existing projection_<stat_type> regressor as-is
    (no new quantile head for count stats) — this only adds the calibration
    layer on top of its Poisson P(over).
    """
    if stat_type not in POISSON_DIST_STAT_TYPES:
        return {'error': f'Unsupported poisson stat_type: {stat_type}', 'stat_type': stat_type}

    from app.services.distribution_calibration import collect_oof_pairs_poisson

    oof_rows = _collect_poisson_oof_rows(stat_type)
    if not oof_rows:
        return {'error': 'No OOF rows available', 'stat_type': stat_type}

    pairs = collect_oof_pairs_poisson(oof_rows)
    try:
        calibrator = fit_isotonic_calibrator(pairs)
    except ValueError:
        return {'error': 'No calibration pairs produced', 'stat_type': stat_type}

    import joblib
    _ensure_model_dir()
    today = date_type.today().isoformat()
    model_name = f'dist_calibrator_{stat_type}'
    filename = f"{model_name}_{today}.pkl"
    filepath = os.path.join(MODEL_DIR, filename)
    joblib.dump(calibrator, filepath)
    artifact_path = persist_model_artifact(filepath, filename)

    ModelMetadata.query.filter_by(model_name=model_name, is_active=True).update({'is_active': False})
    db.session.add(ModelMetadata(
        model_name=model_name,
        model_type='isotonic_calibrator',
        version=f"{stat_type}_{today}",
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(pairs),
        is_active=True,
        metadata_json=json.dumps({'oof_pairs': len(pairs), 'oof_rows': len(oof_rows)}),
    ))
    db.session.commit()

    return {
        'stat_type': stat_type,
        'calibration_pairs': len(pairs),
        'oof_rows': len(oof_rows),
        'model_path': artifact_path,
    }


def retrain_all_distributional_models() -> dict:
    """Retrain all distributional heads + their calibrators.

    Called by `flask retrain --force` (extended in Task 7) or directly.
    """
    results = {}
    for stat_type in DIST_STAT_TYPES:
        results[stat_type] = train_distributional_model(stat_type)
    for stat_type in POISSON_DIST_STAT_TYPES:
        results[stat_type] = train_distributional_calibrator_for_poisson_stat(stat_type)
    return results


def backtest_verdict(dist_ece: float, gauss_ece: float, gate: float = 0.03) -> str:
    """Promotion gate (Phase 1 spec / Plan C design spec): PROMOTE only if
    the distributional ECE clears the absolute gate AND beats the
    incumbent's ECE on the same held-out data; otherwise HOLD.
    """
    if dist_ece <= gate and dist_ece <= gauss_ece:
        return 'PROMOTE'
    return 'HOLD'
```

- [ ] **Step 4: Run distributional_model tests to verify they pass**

Run: `SECRET_KEY=test python -m unittest tests.test_distributional_model -v`
Expected: all tests pass, including the ones from Tasks 3-4 that already existed (no regressions) and the new Task 7 tests.

- [ ] **Step 5: Extend `cli_retrain` and add `cli_backtest` in `app/cli/model_commands.py`**

In `cli_retrain`'s `if force:` branch (currently `app/cli/model_commands.py:62-70`), add after the `market_result = train_market_models()` line and before `click.echo('Done.')`:

```python
        from app.services.distributional_model import retrain_all_distributional_models
        click.echo('--force: training distributional heads + calibrators (Plan C, shadow path)...')
        dist_result = retrain_all_distributional_models()
        click.echo(f'Distributional retrain: {dist_result}')
```

Add a new command (place it after `cli_model_accuracy`, before `cli_model_status`):

```python
@click.command('backtest')
@click.option(
    '--stat-type', default='player_points', show_default=True,
    help='Distributional stat type to backtest (quantile: player_points/'
         'player_rebounds/player_assists/player_points_rebounds_assists; '
         'poisson: player_threes/player_steals/player_blocks).',
)
def cli_backtest(stat_type):
    """Walk-forward reliability comparison: calibrated distributional P(over)
    vs. the incumbent synthetic-Gaussian P(over), on the same held-out rows.

    Promotion gate (Plan C Increment 1): out-of-fold ECE <= 0.03 AND better
    than the incumbent's ECE. This command is a read-only diagnostic run
    after `flask retrain --force` — it logs a JobLog verdict but does not
    itself flip any is_active state (dist_<stat> activation already happens
    at train time).
    """
    import math as _math
    import time as _time

    from app import db
    from app.models import JobLog
    from app.services.distribution import median_from_quantiles, prob_over, prob_over_poisson, rectify_quantiles
    from app.services.distributional_model import (
        DIST_STAT_TYPES,
        POISSON_DIST_STAT_TYPES,
        QUANTILE_ALPHAS,
        _build_dist_training_rows,
        _date_cutoff_split,
        backtest_verdict,
    )
    from app.services.distributional_predictor import load_calibrator, load_quantile_model
    from app.services.distribution_calibration import apply_calibrator
    from app.services.ml_model import load_active_model
    from app.services.pick_quality_model import compute_calibration_metrics

    click.echo(f'=== Distributional Backtest: {stat_type} ===')
    _t0 = _time.perf_counter()

    if stat_type in DIST_STAT_TYPES:
        rows = _build_dist_training_rows(stat_type)
    elif stat_type in POISSON_DIST_STAT_TYPES:
        from app.services.ml_model import _build_training_rows as _build_point_rows
        rows = _build_point_rows(stat_type)
    else:
        click.echo(f'Unsupported stat_type: {stat_type}')
        return

    if not rows:
        click.echo('No training rows available for backtest.')
        return

    _, val_idx, _, _ = _date_cutoff_split(rows)
    if not val_idx:
        click.echo('No held-out rows available for backtest.')
        return

    dist_pairs = []
    gaussian_pairs = []

    if stat_type in DIST_STAT_TYPES:
        model, feature_names = load_quantile_model(stat_type)
        if model is None:
            click.echo(f'No active dist_{stat_type} model — run `flask retrain --force` first.')
            return
        calibrator = load_calibrator(stat_type)
        import numpy as np
        from scipy.stats import norm
        for idx in val_idx:
            _, _, features, target = rows[idx]
            X = np.array([[features.get(k, 0) for k in feature_names]])
            raw_q = rectify_quantiles(model.predict(X)[0].tolist())
            median = median_from_quantiles(QUANTILE_ALPHAS, raw_q)
            std_proxy = max((raw_q[-1] - raw_q[0]) / 4.0, 0.5)
            for offset in (-6.0, -3.0, 0.0, 3.0, 6.0):
                line = median + offset
                p_dist = prob_over(line, QUANTILE_ALPHAS, raw_q)
                if calibrator is not None:
                    p_dist = apply_calibrator(calibrator, p_dist)
                y = 1.0 if target > line else 0.0
                dist_pairs.append((p_dist, y))
                p_gauss = float(1.0 - norm.cdf(line, loc=median, scale=std_proxy))
                gaussian_pairs.append((p_gauss, y))
    else:
        model, feature_names = load_active_model(stat_type)
        if model is None:
            click.echo(f'No active projection_{stat_type} model — run `flask retrain --force` first.')
            return
        calibrator = load_calibrator(stat_type)
        import numpy as np
        from scipy.stats import norm
        for idx in val_idx:
            _, _, features, target = rows[idx]
            X = np.array([[features.get(k, 0) for k in feature_names]])
            lam = float(model.predict(X)[0])
            if lam <= 0:
                continue
            for offset_frac in (-0.9, -0.6, 0.0, 0.6, 0.9):
                candidate = lam + offset_frac * max(lam, 1.0)
                line = max(0.5, _math.floor(candidate) + 0.5)
                p_dist = prob_over_poisson(line, lam)
                if calibrator is not None:
                    p_dist = apply_calibrator(calibrator, p_dist)
                y = 1.0 if target > line else 0.0
                dist_pairs.append((p_dist, y))
                p_gauss = float(1.0 - norm.cdf(line, loc=lam, scale=max(lam ** 0.5, 0.5)))
                gaussian_pairs.append((p_gauss, y))

    if not dist_pairs:
        click.echo('No evaluable held-out pairs produced.')
        return

    dist_metrics = compute_calibration_metrics(dist_pairs, bins=5)
    gauss_metrics = compute_calibration_metrics(gaussian_pairs, bins=5)
    elapsed = _time.perf_counter() - _t0

    click.echo(f"Held-out pairs: {len(dist_pairs)}")
    click.echo(
        f"Distributional  ECE={dist_metrics['ece']:.4f}  "
        f"Brier={dist_metrics['brier']:.4f}  LogLoss={dist_metrics['logloss']:.4f}"
    )
    click.echo(
        f"Incumbent (Gaussian) ECE={gauss_metrics['ece']:.4f}  "
        f"Brier={gauss_metrics['brier']:.4f}  LogLoss={gauss_metrics['logloss']:.4f}"
    )
    click.echo(f"Backtest wall time: {elapsed:.1f}s")

    verdict = backtest_verdict(dist_metrics['ece'], gauss_metrics['ece'])
    message = (
        f"stat={stat_type} dist_ece={dist_metrics['ece']:.4f} "
        f"gauss_ece={gauss_metrics['ece']:.4f} verdict={verdict}"
    )
    db.session.add(JobLog(
        job_name='distributional_backtest',
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        status='success' if verdict == 'PROMOTE' else 'warn',
        message=message[:500],
    ))
    db.session.commit()

    click.echo(f"\nVerdict: {verdict}  (gate: ECE <= 0.03 and beats incumbent)")
```

Register it in `register_model_commands` (currently `app/cli/model_commands.py:1083-1097`):

```python
def register_model_commands(app):
    app.cli.add_command(cli_run_projections)
    app.cli.add_command(cli_grade_bets)
    app.cli.add_command(cli_retrain)
    app.cli.add_command(cli_bootstrap_pick_quality)
    app.cli.add_command(cli_drift_report)
    app.cli.add_command(cli_model_calibration_report)
    app.cli.add_command(cli_model_accuracy)
    app.cli.add_command(cli_model_status)
    app.cli.add_command(cli_prod_readiness)
    app.cli.add_command(cli_backfill_pick_context)
    app.cli.add_command(cli_normalize_pick_context_flags)
    app.cli.add_command(cli_pollution_report)
    app.cli.add_command(cli_backfill_postmortems)
    app.cli.add_command(cli_postmortem_report)
    app.cli.add_command(cli_backtest)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `SECRET_KEY=test python -m unittest tests.test_services -v -k TestModelCommandsStatus`
Expected: all tests pass, including `test_cli_retrain_force_also_trains_distributional_heads`, `test_backtest_cli_no_active_model`, `test_backtest_cli_unsupported_stat_type`, and every pre-existing test in the class.

- [ ] **Step 7: Lint**

Run: `ruff check app/services/distributional_model.py app/cli/model_commands.py tests/test_distributional_model.py tests/test_services.py`
Expected: no issues.

- [ ] **Step 8: Commit**

```bash
git add app/services/distributional_model.py app/cli/model_commands.py \
        tests/test_distributional_model.py tests/test_services.py
git commit -m "feat: extend retrain CLI + add backtest gate for distributional heads"
```

---

### Task 8: Verification + docs

**Files:**
- Modify: `CLAUDE.md` (one line under "Key Conventions")
- Modify: `docs/superpowers/specs/2026-07-13-plan-c-distributional-core-design.md` (Status line)

**Interfaces:**
- Consumes: the full test suite from Tasks 1-7.
- Produces: nothing new — this task only verifies and documents.

- [ ] **Step 1: Run the full test suite with coverage**

Run:
```bash
source .venv/bin/activate && SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
```
Expected: every test passes (existing suite + all new modules from Tasks 1-7). If anything fails, apply `superpowers:systematic-debugging` before proceeding — do not skip or delete a failing test to make this pass.

- [ ] **Step 2: Check the coverage gate**

Run:
```bash
python -m coverage report --include="app/*"
```
Expected: total coverage >= 80%. All five new modules (`distribution.py`, `distribution_calibration.py`, `distributional_model.py`, `distributional_predictor.py`) should individually show high coverage given the density of unit tests in Tasks 1-7; if any new module is below ~85%, add a targeted test for the uncovered branch (e.g. the `ImportError` fallback in `predict_stat`-style guards, or an unreachable-in-tests logging line) rather than lowering the bar.

- [ ] **Step 3: Lint and security scan**

Run:
```bash
ruff check .
bandit -q -r app -x tests -ll
```
Expected: both clean. If `bandit` flags the `joblib.load(...)` calls in `distributional_predictor.load_calibrator` or `distributional_model`'s calibrator-loading path, this is an accepted, pre-existing pattern in this codebase (`pick_quality_model.py` already does `joblib.load`/`joblib.dump` for calibrated Model 2 artifacts) — do not add a `# nosec` suppression unless CI actually fails on it; if it does fail, match whatever suppression (if any) `pick_quality_model.py` uses for its own `joblib.load`, for consistency.

- [ ] **Step 4: Offline end-to-end smoke check (no network, no `instance/app.db`)**

Run this as a one-off Python script against the same in-memory/testing app the test suite uses — it exercises train → predict → calibrate → backtest-compare in one pass, entirely offline:

```bash
source .venv/bin/activate && SECRET_KEY=test python3 - <<'EOF'
from datetime import date, timedelta
from app import create_app, db
from app.models import PlayerGameLog

app = create_app(testing=True)
with app.app_context():
    db.drop_all()
    db.create_all()

    for pid in ('9001', '9002', '9003'):
        for i in range(40):
            offset = int(pid)
            db.session.add(PlayerGameLog(
                player_id=pid, player_name=f'Smoke Player {pid}', team_abbr='TST',
                game_date=date(2024, 1, 1) + timedelta(days=i),
                matchup='TST vs. OPP' if i % 2 == 0 else 'TST @ OPP',
                minutes=32.0,
                pts=max(20.0 + ((i + offset) % 9) - 4, 0.0),
                reb=max(6.0 + ((i + offset) % 5) - 2, 0.0),
                ast=max(5.0 + ((i + offset) % 4) - 1, 0.0),
                fg3m=2.0, stl=1.0, blk=0.5, tov=2.0,
                fgm=8.0, fga=17.0, ftm=4.0, fta=5.0, fg3a=6.0,
                home_away='home' if i % 2 == 0 else 'away',
            ))
    db.session.commit()

    from unittest.mock import patch
    from app.services import distributional_model as dm

    with patch.object(dm, 'MIN_TRAIN_SAMPLES', 50):
        train_result = dm.train_distributional_model('player_points')
    print('TRAIN:', train_result)
    assert 'error' not in train_result
    assert train_result['calibrator_fitted']

    rows = dm._build_dist_training_rows('player_points')
    _, _, features, _ = rows[-1]

    from app.services.distributional_predictor import predict_distribution, predict_prob_over
    dist = predict_distribution('player_points', features)
    print('DISTRIBUTION:', dist)
    assert dist is not None and dist['kind'] == 'quantile'

    p_over = predict_prob_over('player_points', features, dist['point'])
    print('P(over median):', p_over)
    assert 0.0 <= p_over <= 1.0

    verdict = dm.backtest_verdict(dist_ece=0.02, gauss_ece=0.05)
    print('BACKTEST VERDICT (synthetic numbers):', verdict)
    assert verdict == 'PROMOTE'

print('Offline distributional smoke check: OK')
EOF
```

Expected output ends with `Offline distributional smoke check: OK` and no exceptions. This never touches `instance/app.db` (uses `create_app(testing=True)`, the same in-memory/test config every unit test uses) and makes no network calls.

- [ ] **Step 5: Update `CLAUDE.md`**

In `/Users/mohamoudmohamed/sports_betting_tracker/CLAUDE.md`, under `## Key Conventions`, change:

```
- Scheduler has 21 registered jobs as of 2026-07-11 (refresh_scenario_splits added in Plan B)
```

to:

```
- Scheduler has 21 registered jobs as of 2026-07-11 (refresh_scenario_splits added in Plan B)
- Plan C Increment 1 (distributional core) shipped: `USE_DISTRIBUTIONAL_MODEL` env flag (default false) gates calibrated quantile/Poisson P(over) in `ValueDetector`; `flask retrain --force` also trains `dist_<stat>` + `dist_calibrator_<stat>` artifacts; `flask backtest --stat-type <stat>` gates promotion on ECE <= 0.03
```

- [ ] **Step 6: Update the design spec's status line**

In `/Users/mohamoudmohamed/sports_betting_tracker/docs/superpowers/specs/2026-07-13-plan-c-distributional-core-design.md`, change:

```
**Status:** Approved design, pending implementation plan
```

to:

```
**Status:** Implemented (Increment 1) — see docs/superpowers/plans/2026-07-13-plan-c-distributional-core.md
```

- [ ] **Step 7: Final full-suite re-run (post-doc-edit sanity check)**

Run:
```bash
source .venv/bin/activate && SECRET_KEY=test python -m coverage run -m unittest discover -s tests -v
```
Expected: still all passing (doc-only changes in Steps 5-6 don't touch app code, but this confirms nothing was accidentally left broken from earlier tasks).

- [ ] **Step 8: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-13-plan-c-distributional-core-design.md
git commit -m "docs: mark Plan C Increment 1 (distributional core) implemented"
```
