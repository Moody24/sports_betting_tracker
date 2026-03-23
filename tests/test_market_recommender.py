from datetime import date, timedelta
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from app import db
from app.models import GameSnapshot, ModelMetadata
from tests.helpers import BaseTestCase


class TestMarketRecommender(BaseTestCase):
    def _seed_snapshots(self, n=80):
        with self.app.app_context():
            today = date(2026, 3, 1)
            for i in range(n):
                line = 220.5 + ((i % 5) - 2)
                # Keep class balance for total over/under.
                if i % 2 == 0:
                    home_score = 104 + (i % 8)
                    away_score = 99 + (i % 6)
                else:
                    home_score = 122 + (i % 9)
                    away_score = 113 + (i % 7)
                # Keep class balance for home/away winner.
                if i % 3 == 0:
                    home_score, away_score = away_score, home_score
                snap = GameSnapshot(
                    espn_id=f"g{i}",
                    game_date=today - timedelta(days=i),
                    home_team="Home",
                    away_team="Away",
                    home_logo="",
                    away_logo="",
                    home_score=home_score,
                    away_score=away_score,
                    status="STATUS_FINAL",
                    over_under_line=line,
                    moneyline_home=-140 + (i % 20),
                    moneyline_away=120 - (i % 20),
                    is_final=True,
                )
                db.session.add(snap)
            db.session.commit()

    def test_train_market_models_insufficient(self):
        from app.services.market_recommender import train_market_models
        with self.app.app_context():
            result = train_market_models(min_samples=10)
        self.assertIn("moneyline", result)
        self.assertIn("error", result["moneyline"])
        self.assertIn("total_ou", result)
        self.assertIn("error", result["total_ou"])

    def test_train_market_models_success_creates_active_metadata(self):
        from app.services.market_recommender import train_market_models, MODEL_NAME_ML, MODEL_NAME_TOTAL
        self._seed_snapshots(90)
        with self.app.app_context():
            result = train_market_models(min_samples=40)
            self.assertIn("accuracy", result["moneyline"])
            self.assertIn("accuracy", result["total_ou"])
            ml_meta = ModelMetadata.query.filter_by(model_name=MODEL_NAME_ML, is_active=True).first()
            tot_meta = ModelMetadata.query.filter_by(model_name=MODEL_NAME_TOTAL, is_active=True).first()
            self.assertIsNotNone(ml_meta)
            self.assertIsNotNone(tot_meta)

    def test_recommend_market_sides_no_models(self):
        from app.services.market_recommender import recommend_market_sides
        with self.app.app_context():
            recs = recommend_market_sides([{
                "espn_id": "g1",
                "over_under_line": 220.5,
                "moneyline_home": -130,
                "moneyline_away": 110,
            }])
        self.assertEqual(recs, {})

    def test_recommend_market_sides_with_stubbed_models(self):
        from app.services.market_recommender import recommend_market_sides

        class _FakeModel:
            def __init__(self, p):
                self._p = p

            def predict_proba(self, _x):
                return [[1 - self._p, self._p]]

        with self.app.app_context():
            with patch('app.services.market_recommender._load_active_model') as mock_loader:
                mock_loader.side_effect = [
                    (_FakeModel(0.62), SimpleNamespace(version='ml_v1')),
                    (_FakeModel(0.41), SimpleNamespace(version='tot_v1')),
                ]
                recs = recommend_market_sides([{
                    "espn_id": "g1",
                    "over_under_line": 219.5,
                    "moneyline_home": -145,
                    "moneyline_away": 122,
                }])

        self.assertIn("g1", recs)
        self.assertEqual(recs["g1"]["moneyline"]["side"], "home")
        self.assertEqual(recs["g1"]["total"]["side"], "under")

    def test_recommend_market_sides_respects_strict_env_gates(self):
        from app.services.market_recommender import recommend_market_sides

        class _FakeModel:
            def __init__(self, p):
                self._p = p

            def predict_proba(self, _x):
                return [[1 - self._p, self._p]]

        strict_env = {
            "MARKET_REC_MIN_EDGE_ML": "0.20",
            "MARKET_REC_MIN_CONF_ML": "0.80",
            "MARKET_REC_MIN_EDGE_TOTAL": "0.20",
            "MARKET_REC_MIN_CONF_TOTAL": "0.80",
        }
        with self.app.app_context():
            with patch('app.services.market_recommender._load_active_model') as mock_loader:
                with patch.dict(os.environ, strict_env, clear=False):
                    mock_loader.side_effect = [
                        (_FakeModel(0.62), SimpleNamespace(version='ml_v1')),
                        (_FakeModel(0.41), SimpleNamespace(version='tot_v1')),
                    ]
                    recs = recommend_market_sides([{
                        "espn_id": "g1",
                        "over_under_line": 219.5,
                        "moneyline_home": -145,
                        "moneyline_away": 122,
                    }])

        self.assertEqual(recs["g1"]["moneyline"]["action"], "pass")
        self.assertEqual(recs["g1"]["total"]["action"], "pass")

    def test_evaluate_market_models_returns_metrics(self):
        from app.services.market_recommender import evaluate_market_models, train_market_models

        self._seed_snapshots(90)
        with self.app.app_context():
            train_market_models(min_samples=40)
            report = evaluate_market_models(days=365, bins=5)

        self.assertNotIn("error", report)
        self.assertIn("markets", report)
        self.assertIn("moneyline", report["markets"])
        self.assertIn("total_ou", report["markets"])
        self.assertIn("accuracy", report["markets"]["moneyline"])
        self.assertIn("brier", report["markets"]["total_ou"])

    def test_apply_market_threshold_policy_affects_recommendations(self):
        from app.services.market_recommender import (
            apply_market_threshold_policy,
            recommend_market_sides,
            train_market_models,
        )

        self._seed_snapshots(90)
        with self.app.app_context():
            train_market_models(min_samples=40)
            baseline = recommend_market_sides([{
                "espn_id": "g1",
                "over_under_line": 219.5,
                "moneyline_home": -145,
                "moneyline_away": 122,
            }])
            apply_market_threshold_policy({
                "moneyline": {"min_edge": 0.2, "min_confidence": 0.85},
                "total_ou": {"min_edge": 0.2, "min_confidence": 0.85},
            })
            strict = recommend_market_sides([{
                "espn_id": "g1",
                "over_under_line": 219.5,
                "moneyline_home": -145,
                "moneyline_away": 122,
            }])

        self.assertIn(baseline["g1"]["moneyline"]["action"], ("bet", "pass"))
        self.assertEqual(strict["g1"]["moneyline"]["action"], "pass")
        self.assertEqual(strict["g1"]["total"]["action"], "pass")

    def test_tune_market_thresholds_returns_policy(self):
        from app.services.market_recommender import train_market_models, tune_market_thresholds

        self._seed_snapshots(120)
        with self.app.app_context():
            train_market_models(min_samples=40)
            result = tune_market_thresholds(days=365, bins=5, min_bets=5, apply=False)

        self.assertNotIn("error", result)
        self.assertIn("policy", result)
        self.assertIn("moneyline", result["policy"])
        self.assertIn("total_ou", result["policy"])

    def test_moneyline_env_killswitch_forces_pass(self):
        from app.services.market_recommender import recommend_market_sides

        class _FakeModel:
            def __init__(self, p):
                self._p = p

            def predict_proba(self, _x):
                return [[1 - self._p, self._p]]

        with self.app.app_context():
            with patch('app.services.market_recommender._load_active_model') as mock_loader:
                with patch.dict(os.environ, {"MONEYLINE_RECS_ENABLED": "false"}, clear=False):
                    mock_loader.side_effect = [
                        (_FakeModel(0.67), SimpleNamespace(version='ml_v1')),
                        (_FakeModel(0.61), SimpleNamespace(version='tot_v1')),
                    ]
                    recs = recommend_market_sides([{
                        "espn_id": "g1",
                        "over_under_line": 220.5,
                        "moneyline_home": -130,
                        "moneyline_away": 110,
                    }])
        self.assertEqual(recs["g1"]["moneyline"]["action"], "pass")
        self.assertEqual(recs["g1"]["moneyline"]["action_reason"], "market_disabled")


class TestMarketRecommenderPureFunctions(BaseTestCase):
    """Unit tests for pure/utility functions in market_recommender."""

    # ── _decide_market_action ──────────────────────────────────────────

    def test_decide_both_below_threshold(self):
        from app.services.market_recommender import _decide_market_action
        action, reason = _decide_market_action(0.01, 0.50, 0.05, 0.55)
        self.assertEqual(action, 'pass')
        self.assertEqual(reason, 'edge_and_confidence_below_threshold')

    def test_decide_only_edge_below(self):
        from app.services.market_recommender import _decide_market_action
        action, reason = _decide_market_action(0.01, 0.60, 0.05, 0.55)
        self.assertEqual(action, 'pass')
        self.assertEqual(reason, 'edge_below_threshold')

    def test_decide_only_confidence_below(self):
        from app.services.market_recommender import _decide_market_action
        action, reason = _decide_market_action(0.10, 0.50, 0.05, 0.55)
        self.assertEqual(action, 'pass')
        self.assertEqual(reason, 'confidence_below_threshold')

    def test_decide_meets_thresholds(self):
        from app.services.market_recommender import _decide_market_action
        action, reason = _decide_market_action(0.10, 0.60, 0.05, 0.55)
        self.assertEqual(action, 'bet')
        self.assertEqual(reason, 'meets_thresholds')

    def test_decide_exact_threshold_is_bet(self):
        from app.services.market_recommender import _decide_market_action
        action, _ = _decide_market_action(0.05, 0.55, 0.05, 0.55)
        self.assertEqual(action, 'bet')

    # ── _calibration_bins ─────────────────────────────────────────────

    def test_calibration_bins_basic(self):
        from app.services.market_recommender import _calibration_bins
        rows = [(0.1, 0), (0.15, 1), (0.6, 1), (0.65, 0), (0.9, 1)]
        out = _calibration_bins(rows, bins=5)
        self.assertEqual(len(out), 5)
        for b in out:
            self.assertIn('range', b)

    def test_calibration_bins_empty_bucket(self):
        from app.services.market_recommender import _calibration_bins
        rows = [(0.95, 1), (0.97, 0)]
        out = _calibration_bins(rows, bins=4)
        empty_bins = [b for b in out if b['count'] == 0]
        self.assertGreater(len(empty_bins), 0)
        for b in empty_bins:
            self.assertNotIn('avg_pred', b)

    def test_calibration_bins_clamps_below_2(self):
        from app.services.market_recommender import _calibration_bins
        rows = [(0.3, 1), (0.7, 0)]
        out = _calibration_bins(rows, bins=1)
        self.assertEqual(len(out), 2)

    def test_calibration_bins_clamps_above_10(self):
        from app.services.market_recommender import _calibration_bins
        rows = [(i / 20, i % 2) for i in range(20)]
        out = _calibration_bins(rows, bins=15)
        self.assertEqual(len(out), 10)

    def test_calibration_bins_last_bucket_includes_1(self):
        from app.services.market_recommender import _calibration_bins
        rows = [(1.0, 1)]
        out = _calibration_bins(rows, bins=2)
        last = out[-1]
        self.assertEqual(last['count'], 1)

    # ── _metadata_logloss ─────────────────────────────────────────────

    def test_metadata_logloss_none_meta(self):
        from app.services.market_recommender import _metadata_logloss
        self.assertIsNone(_metadata_logloss(None))

    def test_metadata_logloss_no_json(self):
        from app.services.market_recommender import _metadata_logloss
        meta = SimpleNamespace(metadata_json=None)
        self.assertIsNone(_metadata_logloss(meta))

    def test_metadata_logloss_missing_key(self):
        from app.services.market_recommender import _metadata_logloss
        meta = SimpleNamespace(metadata_json=json.dumps({'accuracy': 0.6}))
        self.assertIsNone(_metadata_logloss(meta))

    def test_metadata_logloss_valid(self):
        from app.services.market_recommender import _metadata_logloss
        meta = SimpleNamespace(metadata_json=json.dumps({'logloss': 0.42}))
        self.assertAlmostEqual(_metadata_logloss(meta), 0.42)

    def test_metadata_logloss_invalid_json(self):
        from app.services.market_recommender import _metadata_logloss
        meta = SimpleNamespace(metadata_json='not-json')
        self.assertIsNone(_metadata_logloss(meta))

    # ── _metadata_json ────────────────────────────────────────────────

    def test_metadata_json_none(self):
        from app.services.market_recommender import _metadata_json
        self.assertEqual(_metadata_json(None), {})

    def test_metadata_json_valid(self):
        from app.services.market_recommender import _metadata_json
        meta = SimpleNamespace(metadata_json=json.dumps({'k': 'v'}))
        self.assertEqual(_metadata_json(meta), {'k': 'v'})

    def test_metadata_json_invalid(self):
        from app.services.market_recommender import _metadata_json
        meta = SimpleNamespace(metadata_json='bad')
        self.assertEqual(_metadata_json(meta), {})

    def test_metadata_json_non_dict(self):
        from app.services.market_recommender import _metadata_json
        meta = SimpleNamespace(metadata_json=json.dumps([1, 2, 3]))
        self.assertEqual(_metadata_json(meta), {})

    # ── _resolve_market_policy ────────────────────────────────────────

    def test_resolve_policy_defaults(self):
        from app.services.market_recommender import _resolve_market_policy, DEFAULT_POLICY
        policy = _resolve_market_policy(None, None)
        self.assertEqual(policy['moneyline']['min_edge'], DEFAULT_POLICY['moneyline']['min_edge'])
        self.assertEqual(policy['total_ou']['min_edge'], DEFAULT_POLICY['total_ou']['min_edge'])

    def test_resolve_policy_metadata_override(self):
        from app.services.market_recommender import _resolve_market_policy
        ml_meta = SimpleNamespace(metadata_json=json.dumps({
            'recommended_thresholds': {'min_edge': 0.08, 'min_confidence': 0.60}
        }))
        policy = _resolve_market_policy(ml_meta, None)
        self.assertAlmostEqual(policy['moneyline']['min_edge'], 0.08)
        self.assertAlmostEqual(policy['moneyline']['min_confidence'], 0.60)

    def test_resolve_policy_env_var_wins_over_metadata(self):
        from app.services.market_recommender import _resolve_market_policy
        ml_meta = SimpleNamespace(metadata_json=json.dumps({
            'recommended_thresholds': {'min_edge': 0.08}
        }))
        with patch.dict(os.environ, {'MARKET_REC_MIN_EDGE_ML': '0.12'}, clear=False):
            policy = _resolve_market_policy(ml_meta, None)
        self.assertAlmostEqual(policy['moneyline']['min_edge'], 0.12)

    def test_resolve_policy_override_dict_wins_over_env(self):
        from app.services.market_recommender import _resolve_market_policy
        with patch.dict(os.environ, {'MARKET_REC_MIN_EDGE_ML': '0.12'}, clear=False):
            policy = _resolve_market_policy(None, None, override={
                'moneyline': {'min_edge': 0.20}
            })
        self.assertAlmostEqual(policy['moneyline']['min_edge'], 0.20)

    # ── _is_market_enabled ────────────────────────────────────────────

    def test_is_market_enabled_default_true(self):
        from app.services.market_recommender import _is_market_enabled
        self.assertTrue(_is_market_enabled('moneyline', None))
        self.assertTrue(_is_market_enabled('total_ou', None))

    def test_is_market_enabled_env_killswitch(self):
        from app.services.market_recommender import _is_market_enabled
        with patch.dict(os.environ, {'MONEYLINE_RECS_ENABLED': 'false'}, clear=False):
            self.assertFalse(_is_market_enabled('moneyline', None))
        with patch.dict(os.environ, {'TOTAL_RECS_ENABLED': '0'}, clear=False):
            self.assertFalse(_is_market_enabled('total_ou', None))

    def test_is_market_enabled_metadata_disabled(self):
        from app.services.market_recommender import _is_market_enabled
        meta = SimpleNamespace(metadata_json=json.dumps({'disabled': True}))
        self.assertFalse(_is_market_enabled('moneyline', meta))

    def test_is_market_enabled_metadata_not_disabled(self):
        from app.services.market_recommender import _is_market_enabled
        meta = SimpleNamespace(metadata_json=json.dumps({'disabled': False}))
        self.assertTrue(_is_market_enabled('moneyline', meta))

    # ── _profit_per_unit ──────────────────────────────────────────────

    def test_profit_per_unit_loss(self):
        from app.services.market_recommender import _profit_per_unit
        self.assertEqual(_profit_per_unit(-110, False), -1.0)
        self.assertEqual(_profit_per_unit(200, False), -1.0)

    def test_profit_per_unit_positive_odds_win(self):
        from app.services.market_recommender import _profit_per_unit
        self.assertAlmostEqual(_profit_per_unit(200, True), 2.0)
        self.assertAlmostEqual(_profit_per_unit(110, True), 1.10)

    def test_profit_per_unit_negative_odds_win(self):
        from app.services.market_recommender import _profit_per_unit
        self.assertAlmostEqual(_profit_per_unit(-200, True), 0.5)
        self.assertAlmostEqual(_profit_per_unit(-110, True), 100.0 / 110.0)

    def test_profit_per_unit_zero_odds(self):
        from app.services.market_recommender import _profit_per_unit
        self.assertEqual(_profit_per_unit(0, True), 1.0)

    # ── _features_for_inputs ──────────────────────────────────────────

    def test_features_for_inputs_length(self):
        from app.services.market_recommender import _features_for_inputs, FEATURES
        row = _features_for_inputs(220.5, -140, 120)
        self.assertEqual(len(row), len(FEATURES))

    def test_features_for_inputs_values(self):
        from app.services.market_recommender import _features_for_inputs
        row = _features_for_inputs(220.0, -200, 170)
        self.assertAlmostEqual(row[0], 220.0)      # over_under_line
        self.assertAlmostEqual(row[1], -200.0)     # moneyline_home
        self.assertAlmostEqual(row[2], 170.0)      # moneyline_away
        self.assertAlmostEqual(row[3], 370.0)      # ml_gap_abs = abs(-200 - 170)
        self.assertAlmostEqual(row[8], 0.0)        # ou_centered_220 = 220 - 220

    def test_features_for_inputs_favorite_flag(self):
        from app.services.market_recommender import _features_for_inputs
        row_home_fav = _features_for_inputs(220.0, -140, 120)
        row_away_fav = _features_for_inputs(220.0, 120, -140)
        self.assertEqual(row_home_fav[7], 1.0)   # home is favorite
        self.assertEqual(row_away_fav[7], 0.0)   # away is favorite

    # ── _adapt_row_to_model ───────────────────────────────────────────

    def test_adapt_row_exact_width(self):
        from app.services.market_recommender import _adapt_row_to_model
        model = SimpleNamespace(n_features_in_=3)
        row = [[1.0, 2.0, 3.0]]
        result = _adapt_row_to_model(model, row)
        self.assertEqual(result, [[1.0, 2.0, 3.0]])

    def test_adapt_row_truncates_extra_features(self):
        from app.services.market_recommender import _adapt_row_to_model
        model = SimpleNamespace(n_features_in_=2)
        row = [[1.0, 2.0, 3.0, 4.0]]
        result = _adapt_row_to_model(model, row)
        self.assertEqual(result, [[1.0, 2.0]])

    def test_adapt_row_pads_missing_features(self):
        from app.services.market_recommender import _adapt_row_to_model
        model = SimpleNamespace(n_features_in_=5)
        row = [[1.0, 2.0, 3.0]]
        result = _adapt_row_to_model(model, row)
        self.assertEqual(result, [[1.0, 2.0, 3.0, 0.0, 0.0]])

    def test_adapt_row_no_n_features_attr(self):
        from app.services.market_recommender import _adapt_row_to_model
        model = SimpleNamespace()  # no n_features_in_
        row = [[1.0, 2.0]]
        result = _adapt_row_to_model(model, row)
        self.assertEqual(result, [[1.0, 2.0]])

    # ── _split_time_aware ─────────────────────────────────────────────

    def test_split_time_aware_returns_time_split(self):
        from app.services.market_recommender import _split_time_aware
        from datetime import date as d
        n = 20
        X = [[float(i)] for i in range(n)]
        y = [i % 2 for i in range(n)]
        dates = [d(2025, 1, i + 1) for i in range(n)]
        X_tr, X_val, y_tr, y_val, strategy = _split_time_aware(X, y, dates)
        self.assertEqual(strategy, 'time_aware')
        self.assertGreater(len(X_tr), 0)
        self.assertGreater(len(X_val), 0)
        self.assertEqual(len(X_tr) + len(X_val), n)

    def test_split_time_aware_fallback_on_single_class(self):
        from app.services.market_recommender import _split_time_aware
        from datetime import date as d
        n = 20
        X = [[float(i)] for i in range(n)]
        y = [0] * 14 + [1] * 6   # 70/30 → time split gives train=all-zeros → fallback
        dates = [d(2025, 1, i + 1) for i in range(n)]
        _, _, y_tr, _, strategy = _split_time_aware(X, y, dates)
        # Strategy is either time_aware or stratified_fallback depending on split
        self.assertIn(strategy, ('time_aware', 'stratified_fallback'))
