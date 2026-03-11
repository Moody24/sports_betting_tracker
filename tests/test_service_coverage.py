"""Tests to improve coverage on low-coverage service files."""

import json
from datetime import datetime, timezone, timedelta, date as date_type
from unittest.mock import patch, MagicMock

from app import db
from app.models import PickContext, ModelMetadata, JobLog
from tests.helpers import BaseTestCase, make_user, make_bet


# ── value_detector pure math ──────────────────────────────────────────


class TestValueDetectorMath(BaseTestCase):
    """Direct unit tests for pure math functions in value_detector."""

    def test_implied_prob_positive_odds(self):
        from app.services.value_detector import implied_prob
        # +200 → 100/300 ≈ 0.333
        self.assertAlmostEqual(implied_prob(200), 1 / 3, places=3)

    def test_implied_prob_negative_odds(self):
        from app.services.value_detector import implied_prob
        # -150 → 150/250 = 0.6
        self.assertAlmostEqual(implied_prob(-150), 0.6, places=3)

    def test_implied_prob_zero_odds(self):
        from app.services.value_detector import implied_prob
        self.assertAlmostEqual(implied_prob(0), 0.5, places=3)

    def test_implied_prob_even_odds(self):
        from app.services.value_detector import implied_prob
        # -100 → 100/200 = 0.5
        self.assertAlmostEqual(implied_prob(-100), 0.5, places=3)
        # +100 → 100/200 = 0.5
        self.assertAlmostEqual(implied_prob(100), 0.5, places=3)

    def test_decimal_odds_positive(self):
        from app.services.value_detector import decimal_odds
        # +200 → 3.0
        self.assertAlmostEqual(decimal_odds(200), 3.0, places=3)

    def test_decimal_odds_negative(self):
        from app.services.value_detector import decimal_odds
        # -200 → 1.5
        self.assertAlmostEqual(decimal_odds(-200), 1.5, places=3)

    def test_decimal_odds_zero(self):
        from app.services.value_detector import decimal_odds
        self.assertAlmostEqual(decimal_odds(0), 2.0, places=3)

    def test_american_from_decimal_above_2(self):
        from app.services.value_detector import american_from_decimal
        # 3.0 → +200
        self.assertEqual(american_from_decimal(3.0), 200)

    def test_american_from_decimal_below_2(self):
        from app.services.value_detector import american_from_decimal
        # 1.5 → -200
        self.assertEqual(american_from_decimal(1.5), -200)

    def test_american_from_decimal_exactly_2(self):
        from app.services.value_detector import american_from_decimal
        # 2.0 → +100
        self.assertEqual(american_from_decimal(2.0), 100)

    def test_american_from_decimal_le_1(self):
        from app.services.value_detector import american_from_decimal
        self.assertEqual(american_from_decimal(1.0), 0)
        self.assertEqual(american_from_decimal(0.5), 0)

    def test_devig_probs_symmetric(self):
        from app.services.value_detector import devig_probs
        over_p, under_p = devig_probs(-110, -110)
        self.assertAlmostEqual(over_p, 0.5, places=2)
        self.assertAlmostEqual(under_p, 0.5, places=2)

    def test_devig_probs_sum_to_one(self):
        from app.services.value_detector import devig_probs
        over_p, under_p = devig_probs(-150, +130)
        self.assertAlmostEqual(over_p + under_p, 1.0, places=5)

    def test_devig_probs_zero_total(self):
        from app.services.value_detector import devig_probs
        over_p, under_p = devig_probs(0, 0)
        self.assertEqual(over_p, 0.5)
        self.assertEqual(under_p, 0.5)

    def test_quarter_kelly_positive_edge(self):
        from app.services.value_detector import quarter_kelly
        stake = quarter_kelly(0.10, -110, 1000)
        self.assertGreater(stake, 0)
        self.assertLessEqual(stake, 50)  # capped at 5% of bankroll

    def test_quarter_kelly_zero_edge(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0, -110, 1000), 0.0)

    def test_quarter_kelly_negative_edge(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(-0.05, -110, 1000), 0.0)

    def test_quarter_kelly_zero_bankroll(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0.10, -110, 0), 0.0)

    def test_quarter_kelly_zero_odds(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0.10, 0, 1000), 0.0)


# ── feature_engine ────────────────────────────────────────────────────


class TestFeatureEngine(BaseTestCase):
    """Tests for feature_engine pure utility functions."""

    def test_prop_to_stat_key_mapping(self):
        from app.services.feature_engine import _prop_to_stat_key
        self.assertEqual(_prop_to_stat_key('player_points'), 'pts')
        self.assertEqual(_prop_to_stat_key('player_rebounds'), 'reb')
        self.assertEqual(_prop_to_stat_key('player_assists'), 'ast')
        self.assertEqual(_prop_to_stat_key('player_threes'), 'fg3m')
        self.assertIsNone(_prop_to_stat_key('unknown'))

    def test_compute_std_single_value(self):
        from app.services.feature_engine import _compute_std
        # Less than 2 logs → 0
        self.assertEqual(_compute_std([MagicMock(pts=10)], 'pts'), 0.0)

    def test_compute_std_multiple(self):
        from app.services.feature_engine import _compute_std
        logs = [MagicMock(pts=10), MagicMock(pts=20), MagicMock(pts=30)]
        result = _compute_std(logs, 'pts')
        self.assertGreater(result, 0)

    def test_average_stat_empty(self):
        from app.services.feature_engine import _average_stat
        self.assertEqual(_average_stat([], 'pts'), 0.0)

    def test_average_stat_values(self):
        from app.services.feature_engine import _average_stat
        logs = [MagicMock(pts=10), MagicMock(pts=20)]
        self.assertAlmostEqual(_average_stat(logs, 'pts'), 15.0, places=1)

    def test_compute_streak_zscore_too_few_logs(self):
        from app.services.feature_engine import _compute_streak_zscore
        logs = [MagicMock(pts=10) for _ in range(5)]
        self.assertEqual(_compute_streak_zscore(logs, 'pts'), 0.0)

    def test_compute_streak_zscore_all_same(self):
        from app.services.feature_engine import _compute_streak_zscore
        logs = [MagicMock(pts=10) for _ in range(15)]
        self.assertEqual(_compute_streak_zscore(logs, 'pts'), 0.0)

    def test_compute_hit_rate_empty(self):
        from app.services.feature_engine import _compute_hit_rate
        self.assertEqual(_compute_hit_rate([], 'pts', 10.0), 0.5)

    def test_compute_hit_rate_zero_line(self):
        from app.services.feature_engine import _compute_hit_rate
        logs = [MagicMock(pts=10)]
        self.assertEqual(_compute_hit_rate(logs, 'pts', 0), 0.5)

    def test_compute_hit_rate_values(self):
        from app.services.feature_engine import _compute_hit_rate
        logs = [MagicMock(pts=20), MagicMock(pts=5), MagicMock(pts=15), MagicMock(pts=3)]
        rate = _compute_hit_rate(logs, 'pts', 10.0)
        self.assertAlmostEqual(rate, 0.5, places=2)  # 2/4

    def test_infer_player_position_center(self):
        from app.services.feature_engine import infer_player_position
        summary = {'season': {'reb': 12.0, 'ast': 2.0, 'fg3m': 0.5}}
        self.assertEqual(infer_player_position(summary), 'c')

    def test_infer_player_position_pf(self):
        from app.services.feature_engine import infer_player_position
        summary = {'season': {'reb': 8.0, 'ast': 3.0, 'fg3m': 1.0}}
        self.assertEqual(infer_player_position(summary), 'pf')

    def test_infer_player_position_pg(self):
        from app.services.feature_engine import infer_player_position
        summary = {'season': {'reb': 4.0, 'ast': 8.0, 'fg3m': 1.0}}
        self.assertEqual(infer_player_position(summary), 'pg')

    def test_infer_player_position_sg(self):
        from app.services.feature_engine import infer_player_position
        summary = {'season': {'reb': 4.0, 'ast': 5.0, 'fg3m': 1.0}}
        self.assertEqual(infer_player_position(summary), 'sg')

    def test_infer_player_position_sf(self):
        from app.services.feature_engine import infer_player_position
        summary = {'season': {'reb': 4.0, 'ast': 2.0, 'fg3m': 3.0}}
        self.assertEqual(infer_player_position(summary), 'sf')

    def test_infer_player_position_default(self):
        from app.services.feature_engine import infer_player_position
        summary = {'season': {'reb': 4.0, 'ast': 2.0, 'fg3m': 1.0}}
        self.assertEqual(infer_player_position(summary), 'sf')

    def test_infer_player_position_empty_summary(self):
        from app.services.feature_engine import infer_player_position
        self.assertEqual(infer_player_position({}), 'sf')
        self.assertEqual(infer_player_position({'season': None}), 'sf')

    @patch('app.services.feature_engine.get_cached_logs', return_value=[])
    @patch('app.services.feature_engine.get_player_stats_summary')
    @patch('app.services.feature_engine.get_team_defense', return_value={})
    @patch('app.services.feature_engine.get_matchup_adjustment', return_value=1.0)
    @patch('app.services.feature_engine.get_pace_factor', return_value=1.0)
    def test_build_projection_features_returns_dict(self, _pace, _adj, _def, mock_summary, _logs):
        from app.services.feature_engine import build_projection_features
        mock_summary.return_value = {
            'games_played': 20,
            'last_5': {'pts': 25, 'minutes': 32},
            'last_10': {'pts': 24},
            'season': {'pts': 23},
            'std_dev': {'pts': 4},
        }
        result = build_projection_features('123', 'player_points', 'LAL', True)
        self.assertIn('avg_stat_last_5', result)
        self.assertIn('opp_def_rating', result)


# ── nba_service sanitization ──────────────────────────────────────────


class TestSanitizeApiError(BaseTestCase):
    """Tests for API key sanitization in error messages."""

    @patch.dict('os.environ', {'ODDS_API_KEY': 'my-secret-key-123'})
    def test_sanitize_api_error_redacts_key(self):
        from app.services.nba_service import _sanitize_api_error
        exc = Exception("429 Error: https://api.example.com?apiKey=my-secret-key-123")
        result = _sanitize_api_error(exc)
        self.assertNotIn('my-secret-key-123', result)
        self.assertIn('***REDACTED***', result)

    @patch.dict('os.environ', {'ODDS_API_KEY': ''})
    def test_sanitize_api_error_no_key(self):
        from app.services.nba_service import _sanitize_api_error
        exc = Exception("Some network error")
        result = _sanitize_api_error(exc)
        self.assertEqual(result, "Some network error")

    @patch.dict('os.environ', {'ODDS_API_KEY': 'secret'})
    def test_sanitize_api_error_key_not_in_message(self):
        from app.services.nba_service import _sanitize_api_error
        exc = Exception("Connection timeout")
        result = _sanitize_api_error(exc)
        self.assertEqual(result, "Connection timeout")


# ── pick_quality_model ────────────────────────────────────────────────


class TestPickQualityModel(BaseTestCase):
    """Tests for pick_quality_model functions."""

    def test_model_name_global(self):
        from app.services.pick_quality_model import _model_name
        self.assertEqual(_model_name(None), 'pick_quality_nba')

    def test_model_name_user(self):
        from app.services.pick_quality_model import _model_name
        self.assertEqual(_model_name(42), 'pick_quality_nba_user_42')

    def test_build_training_data_insufficient(self):
        from app.services.pick_quality_model import _build_training_data
        with self.app.app_context():
            features, targets, dates = _build_training_data()
            self.assertIsNone(features)
            self.assertIsNone(targets)

    def test_no_model_result(self):
        from app.services.pick_quality_model import _no_model_result
        result = _no_model_result()
        self.assertEqual(result['win_probability'], 0.5)
        self.assertEqual(result['recommendation'], 'no_model')
        self.assertEqual(result['red_flags'], [])

    def test_predict_pick_quality_no_model(self):
        from app.services.pick_quality_model import predict_pick_quality
        with self.app.app_context():
            result = predict_pick_quality({})
            self.assertEqual(result['recommendation'], 'no_model')

    def test_get_feature_importance_no_model(self):
        from app.services.pick_quality_model import get_feature_importance
        with self.app.app_context():
            result = get_feature_importance()
            self.assertEqual(result, [])

    def test_get_calibration_report_no_picks(self):
        from app.services.pick_quality_model import get_calibration_report
        with self.app.app_context():
            result = get_calibration_report()
            self.assertIn('error', result)

    def test_get_calibration_report_invalid_params(self):
        from app.services.pick_quality_model import get_calibration_report
        with self.app.app_context():
            # Passing bad types — should use defaults
            result = get_calibration_report(limit='abc', bins=None)
            self.assertIn('error', result)

    def test_build_training_data_with_picks(self):
        from app.services.pick_quality_model import _build_training_data, MIN_RESOLVED_PICKS
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            for i in range(MIN_RESOLVED_PICKS + 5):
                b = make_bet(user.id, outcome='win' if i % 2 == 0 else 'lose')
                db.session.add(b)
                db.session.flush()
                ctx = {'projected_stat': 20.0, 'projected_edge': 0.1,
                       'model1_vs_line_diff': 2.0, 'player_variance': 5,
                       'player_games_this_season': 30, 'player_hit_rate_vs_line': 0.55,
                       'opp_defense_rating': 110, 'opp_pace': 100,
                       'opp_matchup_adj': 1.0, 'back_to_back': False,
                       'home_game': True, 'days_rest': 2, 'prop_line': 18.5,
                       'american_odds': -110, 'line_vs_season_avg': 1.5,
                       'player_last5_trend': 'hot', 'minutes_trend': 'stable',
                       'confidence_tier': 'strong', 'injury_returning': False}
                db.session.add(PickContext(
                    bet_id=b.id, context_json=json.dumps(ctx),
                ))
            db.session.commit()

            features, targets, dates = _build_training_data()
            self.assertIsNotNone(features)
            self.assertEqual(len(features), MIN_RESOLVED_PICKS + 5)
            self.assertEqual(len(targets), MIN_RESOLVED_PICKS + 5)

    def test_build_training_data_invalid_json(self):
        from app.services.pick_quality_model import _build_training_data, MIN_RESOLVED_PICKS
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            # Include real matchup context so rows aren't filtered as polluted
            clean_ctx = {
                'projected_stat': 10,
                'opp_defense_rating': 110.0,
                'opp_pace': 99.5,
                'opp_matchup_adj': 1.02,
            }
            for i in range(MIN_RESOLVED_PICKS + 5):
                b = make_bet(user.id, outcome='win')
                db.session.add(b)
                db.session.flush()
                # Half with invalid JSON
                cj = 'not-json' if i % 2 == 0 else json.dumps(clean_ctx)
                db.session.add(PickContext(bet_id=b.id, context_json=cj))
            db.session.commit()

            features, targets, dates = _build_training_data()
            # Only valid JSON rows should be returned
            self.assertIsNotNone(features)
            # The valid ones should have parsed
            self.assertGreater(len(features), 0)


# ── pick quality pollution detection ──────────────────────────────────


class TestPickQualityPollutionFilter(BaseTestCase):
    """Tests for _is_polluted_context and bootstrap exclusion in Model 2 training."""

    def test_is_polluted_context_all_zeros(self):
        from app.services.pick_quality_model import _is_polluted_context
        ctx = {'opp_defense_rating': 0, 'opp_pace': 0, 'opp_matchup_adj': 0}
        self.assertTrue(_is_polluted_context(ctx))

    def test_is_polluted_context_missing_keys(self):
        from app.services.pick_quality_model import _is_polluted_context
        self.assertTrue(_is_polluted_context({}))

    def test_is_polluted_context_clean(self):
        from app.services.pick_quality_model import _is_polluted_context
        ctx = {'opp_defense_rating': 110.5, 'opp_pace': 99.0, 'opp_matchup_adj': 1.05}
        self.assertFalse(_is_polluted_context(ctx))

    def test_is_polluted_context_partial_zeros(self):
        from app.services.pick_quality_model import _is_polluted_context
        # Only one zero — not all three; should not be considered polluted
        ctx = {'opp_defense_rating': 110.5, 'opp_pace': 0, 'opp_matchup_adj': 1.05}
        self.assertFalse(_is_polluted_context(ctx))

    def test_build_training_data_excludes_bootstrap(self):
        """_build_training_data excludes AUTO_BOOTSTRAP_HIDDEN bets by default."""
        from app.services import pick_quality_model
        clean_ctx = json.dumps({
            'opp_defense_rating': 110.0, 'opp_pace': 99.5, 'opp_matchup_adj': 1.02,
        })
        with self.app.app_context():
            user = make_user('pqboot', 'pqboot@ex.com')
            db.session.add(user)
            db.session.commit()
            # Create 3 bootstrap bets and 2 real bets
            for i in range(3):
                b = make_bet(user.id, outcome='win', source='auto_generated',
                             notes='AUTO_BOOTSTRAP_HIDDEN:model2')
                db.session.add(b)
                db.session.flush()
                db.session.add(PickContext(bet_id=b.id, context_json=clean_ctx))
            for i in range(2):
                b = make_bet(user.id, outcome='lose')
                db.session.add(b)
                db.session.flush()
                db.session.add(PickContext(bet_id=b.id, context_json=clean_ctx))
            db.session.commit()

            with patch.object(pick_quality_model, 'MIN_RESOLVED_PICKS', 2):
                features, targets, dates = pick_quality_model._build_training_data()
            # Should only include the 2 real bets, not the 3 bootstrap bets
            self.assertIsNotNone(features)
            self.assertEqual(len(features), 2)
            self.assertEqual(targets, [0, 0])

    def test_build_training_data_skips_polluted_context(self):
        """Rows with all-zero matchup context are skipped in training."""
        from app.services import pick_quality_model
        polluted_ctx = json.dumps({'projected_stat': 25.0})  # no matchup keys
        clean_ctx = json.dumps({
            'projected_stat': 25.0,
            'opp_defense_rating': 110.0, 'opp_pace': 99.5, 'opp_matchup_adj': 1.02,
        })
        with self.app.app_context():
            user = make_user('pqpoll', 'pqpoll@ex.com')
            db.session.add(user)
            db.session.commit()
            # 2 polluted + 2 clean
            for ctx_json in [polluted_ctx, polluted_ctx, clean_ctx, clean_ctx]:
                b = make_bet(user.id, outcome='win')
                db.session.add(b)
                db.session.flush()
                db.session.add(PickContext(bet_id=b.id, context_json=ctx_json))
            db.session.commit()

            with patch.object(pick_quality_model, 'MIN_RESOLVED_PICKS', 2):
                features, targets, dates = pick_quality_model._build_training_data()
            # Only the 2 clean rows should pass
            self.assertIsNotNone(features)
            self.assertEqual(len(features), 2)


# ── pollution report CLI ─────────────────────────────────────────────


class TestPollutionReportCLI(BaseTestCase):
    """Test the pollution_report CLI command."""

    def test_pollution_report_basic(self):
        """CLI command runs without errors and reports counts."""
        from click.testing import CliRunner
        with self.app.app_context():
            user = make_user('pruser', 'pruser@ex.com')
            db.session.add(user)
            db.session.commit()
            # Create one clean and one polluted bet
            b1 = make_bet(user.id, outcome='win')
            db.session.add(b1)
            db.session.flush()
            db.session.add(PickContext(
                bet_id=b1.id,
                context_json=json.dumps({
                    'opp_defense_rating': 110, 'opp_pace': 99, 'opp_matchup_adj': 1.0,
                }),
            ))
            b2 = make_bet(user.id, outcome='lose',
                          notes='AUTO_BOOTSTRAP_HIDDEN:model2',
                          source='auto_generated')
            db.session.add(b2)
            db.session.flush()
            db.session.add(PickContext(bet_id=b2.id, context_json='{}'))
            db.session.commit()

        runner = CliRunner()
        result = runner.invoke(self.app.cli, ['pollution_report'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Data Pollution Report', result.output)
        self.assertIn('Clean (real matchup data): 1', result.output)
        self.assertIn('Polluted (zeroed matchup): 1', result.output)


# ── scheduler drift check ─────────────────────────────────────────────


class TestSchedulerDriftMinimum(BaseTestCase):
    """Test drift check with minimum sample size."""

    def test_drift_check_skips_when_too_few_bets(self):
        from app.services import scheduler as sched
        with self.app.app_context():
            user = make_user('driftmin', 'driftmin@ex.com')
            db.session.add(user)
            db.session.commit()
            # Only 10 bets — below 50 minimum
            for i in range(10):
                b = make_bet(user.id, outcome='win',
                             match_date=datetime.now(timezone.utc))
                db.session.add(b)
                db.session.flush()
                db.session.add(PickContext(bet_id=b.id, context_json='{}'))
            db.session.add(ModelMetadata(
                model_name='pick_quality_nba',
                model_type='xgboost_classifier',
                version='drift_min_v1',
                file_path='/tmp/model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=200,
                val_accuracy=0.55,
                is_active=True,
            ))
            db.session.commit()

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched.check_model_drift()

        with self.app.app_context():
            # No warning should be generated (too few bets)
            warn_logs = JobLog.query.filter_by(
                job_name='drift_check', status='warn').all()
            self.assertEqual(len(warn_logs), 0)


# ── scheduler job logging ─────────────────────────────────────────────


class TestSchedulerJobLog(BaseTestCase):
    """Test _log_job wrapper."""

    def test_log_job_success(self):
        from app.services import scheduler as sched
        called = []

        def dummy_job():
            with self.app.app_context():
                called.append(True)

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched._log_job('test_job', dummy_job)

        self.assertTrue(called)
        with self.app.app_context():
            log = JobLog.query.filter_by(job_name='test_job').first()
            self.assertIsNotNone(log)
            self.assertEqual(log.status, 'success')

    def test_log_job_failure(self):
        from app.services import scheduler as sched

        def failing_job():
            raise ValueError("boom")

        with self.app.app_context():
            with patch.object(sched, '_get_app', return_value=self.app):
                sched._log_job('fail_job', failing_job)

        with self.app.app_context():
            log = JobLog.query.filter_by(job_name='fail_job').first()
            self.assertIsNotNone(log)
            self.assertEqual(log.status, 'failed')
            self.assertIn('boom', log.message)


# ── projection engine ─────────────────────────────────────────────────


class TestProjectionEngine(BaseTestCase):
    """Tests for ProjectionEngine."""

    @patch('app.services.projection_engine.find_player_id', return_value=None)
    def test_project_stat_unknown_player(self, _mock):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            result = engine.project_stat('Unknown Player', 'player_points')
            self.assertEqual(result['projection'], 0)
            self.assertEqual(result['confidence'], 'low')

    def test_project_stat_invalid_prop_type(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            result = engine.project_stat('LeBron James', 'invalid_stat')
            self.assertEqual(result['projection'], 0)

    @patch('app.services.projection_engine.find_player_id', return_value='12345')
    @patch('app.services.projection_engine.get_cached_logs')
    @patch('app.services.projection_engine.get_player_stats_summary')
    @patch('app.services.projection_engine.get_matchup_adjustment', return_value=1.0)
    @patch('app.services.projection_engine.get_position_matchup_adjustment', return_value=1.0)
    @patch('app.services.projection_engine.get_pace_factor', return_value=1.0)
    @patch('app.services.projection_engine.get_game_context', return_value={})
    def test_project_stat_with_mocked_data(self, _ctx, _pace, _pos, _adj,
                                            mock_summary, mock_logs, _find):
        from app.services.projection_engine import ProjectionEngine
        mock_log = MagicMock()
        mock_log.pts = 25
        mock_log.minutes = 35
        mock_logs.return_value = [mock_log] * 20
        mock_summary.return_value = {
            'games_played': 20,
            'last_5': {'pts': 26, 'minutes': 34},
            'last_10': {'pts': 25},
            'season': {'pts': 24, 'minutes': 33},
            'std_dev': {'pts': 4},
        }
        with self.app.app_context():
            engine = ProjectionEngine()
            result = engine.project_stat('LeBron James', 'player_points',
                                         'Boston Celtics', 'Los Angeles Lakers', True)
            self.assertGreater(result['projection'], 0)
            self.assertIn(result['confidence'], ('low', 'medium', 'high'))
            self.assertIn('breakdown', result)

    def test_compute_confidence_levels(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        self.assertEqual(engine._compute_confidence(5, 4, 20), 'low')
        self.assertEqual(engine._compute_confidence(30, 2, 20), 'high')
        self.assertEqual(engine._compute_confidence(15, 3, 20), 'medium')
        self.assertEqual(engine._compute_confidence(10, 15, 20), 'low')  # high CV

    def test_empty_projection(self):
        from app.services.projection_engine import ProjectionEngine
        engine = ProjectionEngine()
        result = engine._empty_projection()
        self.assertEqual(result['projection'], 0)
        self.assertEqual(result['games_played'], 0)


# ── nba_service helpers ───────────────────────────────────────────────


class TestNBAServiceHelpers(BaseTestCase):
    """Tests for nba_service helper functions."""

    def test_matchup_key_normalization(self):
        from app.services.nba_service import _matchup_key
        k1 = _matchup_key("LA Clippers", "Boston Celtics")
        k2 = _matchup_key("Los Angeles Clippers", "Boston Celtics")
        self.assertEqual(k1, k2)

    def test_normalize_team_name_aliases(self):
        from app.services.nba_service import _normalize_team_name
        self.assertEqual(_normalize_team_name("LA Clippers"), "los angeles clippers")
        self.assertEqual(_normalize_team_name("LA Lakers"), "los angeles lakers")
        self.assertEqual(_normalize_team_name(""), "")

    def test_coerce_match_date_datetime(self):
        from app.services.nba_service import _coerce_match_date
        bet = MagicMock()
        dt = datetime(2025, 1, 15, tzinfo=timezone.utc)
        bet.match_date = dt
        self.assertEqual(_coerce_match_date(bet), dt)

    def test_coerce_match_date_date(self):
        from app.services.nba_service import _coerce_match_date
        bet = MagicMock()
        bet.match_date = date_type(2025, 1, 15)
        result = _coerce_match_date(bet)
        self.assertEqual(result.year, 2025)

    def test_coerce_match_date_none(self):
        from app.services.nba_service import _coerce_match_date
        bet = MagicMock()
        bet.match_date = None
        self.assertIsNone(_coerce_match_date(bet))

    def test_et_date_str_format(self):
        from app.services.nba_service import _et_date_str
        result = _et_date_str()
        # Should be YYYY-MM-DD format
        self.assertRegex(result, r'\d{4}-\d{2}-\d{2}')


# ── value_detector score_prop ─────────────────────────────────────────


class TestValueDetectorScoreProp(BaseTestCase):
    """Tests for ValueDetector.score_prop."""

    @patch('app.services.value_detector.predict_pick_quality')
    def test_score_prop_basic(self, mock_pq):
        from app.services.value_detector import ValueDetector
        mock_pq.return_value = {'win_probability': 0.5, 'recommendation': 'no_model'}

        with self.app.app_context():
            engine = MagicMock()
            engine.project_stat.return_value = {
                'projection': 25.0,
                'std_dev': 5.0,
                'games_played': 30,
                'confidence': 'high',
                'context_notes': [],
                'z_score': 0.5,
                'projection_source': 'heuristic',
                'breakdown': {'season_avg': 24.0},
            }
            detector = ValueDetector(engine)
            score = detector.score_prop(
                'LeBron James', 'player_points', 20.5, -110, -110
            )
            self.assertEqual(score['player'], 'LeBron James')
            self.assertEqual(score['prop_type'], 'player_points')
            self.assertEqual(score['line'], 20.5)
            self.assertGreater(score['projection'], 0)
            self.assertIn(score['recommended_side'], ('over', 'under'))

    def test_score_prop_insufficient_games(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            engine = MagicMock()
            engine.project_stat.return_value = {
                'projection': 10.0,
                'std_dev': 3.0,
                'games_played': 3,  # too few
                'confidence': 'low',
                'context_notes': [],
                'z_score': 0,
                'projection_source': 'heuristic',
                'breakdown': {},
            }
            detector = ValueDetector(engine)
            score = detector.score_prop(
                'Rookie', 'player_points', 10.5, -110, -110
            )
            self.assertEqual(score['confidence_tier'], 'no_edge')
            self.assertEqual(score['projection'], 0)

    def test_filter_plays(self):
        from app.services.value_detector import ValueDetector
        plays = [
            {'edge': 0.20, 'confidence_tier': 'strong', 'games_played': 30},
            {'edge': 0.01, 'confidence_tier': 'no_edge', 'games_played': 30},
            {'edge': 0.10, 'confidence_tier': 'moderate', 'games_played': 5},
            {'edge': 0.05, 'confidence_tier': 'slight', 'games_played': 30},
        ]
        filtered = ValueDetector.filter_plays(plays)
        self.assertEqual(len(filtered), 2)  # strong + slight (games >= 10)


# ── favicon route ─────────────────────────────────────────────────────


class TestFaviconRoute(BaseTestCase):
    """Test favicon route returns 204 when no file exists."""

    def test_favicon_returns_204_when_no_file(self):
        resp = self.client.get('/favicon.ico')
        self.assertIn(resp.status_code, (200, 204))


# ── health route ──────────────────────────────────────────────────────


class TestHealthRoute(BaseTestCase):
    """Test health endpoint."""

    def test_health_returns_200(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'healthy')


# ── scheduler ensure autopicks user ──────────────────────────────────


class TestEnsureAutopicksUser(BaseTestCase):
    """Test _ensure_autopicks_user uses random password."""

    def test_creates_user_with_random_password(self):
        from app.services.scheduler import _ensure_autopicks_user
        from app.models import User
        with self.app.app_context():
            user = _ensure_autopicks_user(db, User)
            self.assertEqual(user.username, '__autopicks__')
            # Should NOT be able to log in with the old hardcoded password
            self.assertFalse(user.check_password('auto-picks-system-user'))

    def test_returns_existing_user(self):
        from app.services.scheduler import _ensure_autopicks_user
        from app.models import User
        with self.app.app_context():
            user1 = _ensure_autopicks_user(db, User)
            db.session.commit()
            user2 = _ensure_autopicks_user(db, User)
            self.assertEqual(user1.id, user2.id)


# ── app init ──────────────────────────────────────────────────────────


class TestAppInit(BaseTestCase):
    """Test app initialization edge cases."""

    def test_is_non_server_invocation_pytest(self):
        from app import _is_non_server_invocation
        self.assertTrue(_is_non_server_invocation(['pytest', 'tests/']))

    def test_is_non_server_invocation_gunicorn(self):
        from app import _is_non_server_invocation
        self.assertFalse(_is_non_server_invocation(['gunicorn', 'app:create_app()']))

    def test_is_non_server_invocation_unittest(self):
        from app import _is_non_server_invocation
        self.assertTrue(_is_non_server_invocation(['python', '-m', 'unittest']))

    def test_is_non_server_invocation_flask(self):
        from app import _is_non_server_invocation
        self.assertTrue(_is_non_server_invocation(['flask', 'db', 'upgrade']))

    def test_is_non_server_invocation_empty(self):
        from app import _is_non_server_invocation
        self.assertFalse(_is_non_server_invocation([]))


# ── scheduler stale job cleanup ───────────────────────────────────────


class TestStaleJobCleanup(BaseTestCase):
    """Test _close_stale_running_jobs."""

    def test_marks_stale_jobs_as_failed(self):
        from app.services.scheduler import _close_stale_running_jobs, STALE_JOB_MINUTES
        with self.app.app_context():
            stale_time = datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_MINUTES + 10)
            log = JobLog(
                job_name='test_stale',
                started_at=stale_time,
                status='running',
            )
            db.session.add(log)
            db.session.commit()

            _close_stale_running_jobs(db, JobLog)

            updated = db.session.get(JobLog, log.id)
            self.assertEqual(updated.status, 'failed')
            self.assertIn('stale', updated.message.lower())

    def test_ignores_recent_running_jobs(self):
        from app.services.scheduler import _close_stale_running_jobs
        with self.app.app_context():
            recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
            log = JobLog(
                job_name='test_recent',
                started_at=recent_time,
                status='running',
            )
            db.session.add(log)
            db.session.commit()

            _close_stale_running_jobs(db, JobLog)

            updated = db.session.get(JobLog, log.id)
            self.assertEqual(updated.status, 'running')
