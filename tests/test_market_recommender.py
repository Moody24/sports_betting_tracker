from datetime import date, timedelta
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
