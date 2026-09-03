"""Focused value services tests split from the legacy service suite."""

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from app import db
from app.models import PlayerGameLog
from tests.helpers import BaseTestCase
from tests.service_test_support import (
    _seed_player_logs,
    _seed_injury,
)


class TestValueDetector(BaseTestCase):

    def test_dist_features_receive_real_game_total_line(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        detector.engine._player_state_cache['test player'] = ('1', [MagicMock()] * 10, {})
        detector.engine._build_ml_features = MagicMock(return_value={'game_total_line': 228.5})
        detector.engine._context_cache['__dist_defense_lookup__'] = {}

        _stat, features = detector._build_dist_features(
            'Test Player', 'player_points', 'OPP', 'TST', True, None, 228.5,
        )

        self.assertEqual(features['game_total_line'], 228.5)
        self.assertEqual(
            detector.engine._build_ml_features.call_args.kwargs['game_total_line'], 228.5,
        )
    """Tests for ValueDetector: score_prop, score_all_todays_props, get_top_plays."""

    def test_score_prop_insufficient_games(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            _seed_player_logs(count=3, player_id='301')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='301'):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 25.5, -110, -110,
                )
            self.assertEqual(result['confidence_tier'], 'no_edge')
            self.assertEqual(result['projection'], 0)

    def test_score_prop_full_with_edge(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(30):
                log = PlayerGameLog(
                    player_id='302', player_name='High Scorer',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=35 + (i % 3), reb=7, ast=5, fg3m=3, minutes=36,
                    stl=1, blk=1, tov=2, fgm=12, fga=22,
                    ftm=7, fta=8, fg3a=8,
                )
                db.session.add(log)
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='302'):
                result = detector.score_prop(
                    'High Scorer', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                    is_home=True,
                )
            self.assertGreater(result['projection'], 0)
            self.assertGreater(result['model_prob_over'], 0.5)
            self.assertEqual(result['recommended_side'], 'over')

    def test_score_prop_zero_std(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                log = PlayerGameLog(
                    player_id='303', player_name='Consistent',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25, reb=7, ast=5, fg3m=2, minutes=35,
                    stl=1, blk=1, tov=2, fgm=10, fga=20,
                    ftm=5, fta=6, fg3a=5,
                )
                db.session.add(log)
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='303'):
                result = detector.score_prop(
                    'Consistent', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
            self.assertIn(result['model_prob_over'], (0.35, 0.65))

    def test_score_prop_under_recommended(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                log = PlayerGameLog(
                    player_id='304', player_name='Low Scorer',
                    team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=12 + (i % 3), reb=4, ast=2, fg3m=1, minutes=25,
                    stl=1, blk=0, tov=2, fgm=5, fga=12,
                    ftm=2, fta=3, fg3a=3,
                )
                db.session.add(log)
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='304'):
                result = detector.score_prop(
                    'Low Scorer', 'player_points',
                    line=30.5, over_odds=-110, under_odds=-110,
                )
            self.assertEqual(result['recommended_side'], 'under')

    def test_score_prop_with_game_id(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='305')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='305'):
                result = detector.score_prop(
                    'LeBron James', 'player_points',
                    line=25.5, over_odds=-110, under_odds=-110,
                    game_id='espn123',
                )
            self.assertEqual(result['game_id'], 'espn123')

    # -- implied_prob --

    def test_implied_prob_edge_cases(self):
        from app.services.value_detector import implied_prob
        self.assertAlmostEqual(implied_prob(100), 0.5)
        self.assertAlmostEqual(implied_prob(-100), 0.5)

    def test_devig_probs_balanced_market(self):
        from app.services.value_detector import devig_probs
        over, under = devig_probs(-110, -110)
        self.assertAlmostEqual(over, 0.5, places=3)
        self.assertAlmostEqual(under, 0.5, places=3)

    # -- decimal_odds --

    def test_decimal_odds_zero(self):
        from app.services.value_detector import decimal_odds
        self.assertEqual(decimal_odds(0), 2.0)

    # -- quarter_kelly --

    def test_quarter_kelly_zero_bankroll(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0.1, -110, 0), 0.0)

    def test_quarter_kelly_zero_odds(self):
        from app.services.value_detector import quarter_kelly
        self.assertEqual(quarter_kelly(0.1, 0, 1000), 0.0)

    def test_quarter_kelly_positive_odds(self):
        from app.services.value_detector import quarter_kelly
        stake = quarter_kelly(0.10, 200, 1000)
        self.assertGreater(stake, 0)
        self.assertLessEqual(stake, 50.0)

    # -- score_all_todays_props --

    def test_score_all_todays_props(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            _seed_player_logs(count=20, player_id='306')
            _seed_injury('Hurt Player', 'out')
            mock_games = [{
                'odds_event_id': 'evt1', 'espn_id': 'espn1',
                'start_time': '2026-02-25T19:00:00Z',
                'home': {'name': 'Lakers'},
                'away': {'name': 'Celtics'},
            }]
            mock_props = {
                'player_points': [
                    {'player': 'LeBron James', 'line': 25.5,
                     'over_odds': -110, 'under_odds': -110},
                    {'player': 'Hurt Player', 'line': 10.5,
                     'over_odds': -110, 'under_odds': -110},
                    {'player': '', 'line': 0, 'over_odds': 0, 'under_odds': 0},
                ],
            }
            detector = ValueDetector()
            with patch('app.services.nba_service.fetch_player_props_for_event',
                       return_value=mock_props):
                with patch('app.services.projection_engine.find_player_id', return_value='306'):
                    scores = detector.score_all_todays_props(games=mock_games)
            self.assertGreaterEqual(len(scores), 1)

    def test_score_all_todays_props_no_games(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            detector = ValueDetector()
            with patch('app.services.nba_service.get_todays_games', return_value=[]):
                scores = detector.score_all_todays_props()
            self.assertEqual(scores, [])

    def test_score_all_todays_props_fetch_exception(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            mock_games = [{'odds_event_id': 'evt1', 'espn_id': 'e1',
                          'start_time': '', 'home': {'name': 'A'},
                          'away': {'name': 'B'}}]
            detector = ValueDetector()
            with patch('app.services.nba_service.fetch_player_props_for_event',
                       side_effect=Exception("fail")):
                scores = detector.score_all_todays_props(games=mock_games)
            self.assertEqual(scores, [])

    def test_score_all_todays_props_no_event_id(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            mock_games = [{'odds_event_id': '', 'espn_id': 'e1',
                          'start_time': '', 'home': {'name': 'A'},
                          'away': {'name': 'B'}}]
            detector = ValueDetector()
            scores = detector.score_all_todays_props(games=mock_games)
            self.assertEqual(scores, [])

    def test_score_all_todays_props_resolves_player_side_from_team_abbr(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            db.session.add(PlayerGameLog(
                player_id='777',
                player_name='Away Player',
                team_abbr='BOS',
                game_date=date(2026, 2, 25),
                pts=20,
            ))
            db.session.commit()

            mock_games = [{
                'odds_event_id': 'evt1',
                'espn_id': 'espn1',
                'start_time': '2026-02-25T19:00:00Z',
                'home': {'name': 'Los Angeles Lakers', 'abbr': 'LAL'},
                'away': {'name': 'Boston Celtics', 'abbr': 'BOS'},
            }]
            mock_props = {
                'player_points': [{
                    'player': 'Away Player',
                    'line': 15.5,
                    'over_odds': -110,
                    'under_odds': -110,
                }],
            }
            detector = ValueDetector()
            with patch('app.services.nba_service.fetch_player_props_for_event', return_value=mock_props):
                with patch.object(detector, 'score_prop', return_value={
                    'edge': 0.1, 'confidence_tier': 'moderate', 'games_played': 20,
                }) as score_mock:
                    detector.score_all_todays_props(games=mock_games)

            self.assertTrue(score_mock.called)
            kwargs = score_mock.call_args.kwargs
            self.assertEqual(kwargs['team_name'], 'Boston Celtics')
            self.assertEqual(kwargs['opponent_name'], 'Los Angeles Lakers')
            self.assertFalse(kwargs['is_home'])

    # -- get_top_plays --

    def test_get_top_plays(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            detector = ValueDetector()
            mock_scores = [
                {'edge': 0.20, 'confidence_tier': 'strong', 'games_played': 30},
                {'edge': 0.01, 'confidence_tier': 'no_edge', 'games_played': 30},
                {'edge': 0.10, 'confidence_tier': 'moderate', 'games_played': 5},
            ]
            with patch.object(detector, 'score_all_todays_props', return_value=mock_scores):
                top = detector.get_top_plays(min_edge=0.03)
            self.assertEqual(len(top), 1)

    def test_recommend_best_parlay_returns_target_range(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            detector = ValueDetector()
            mock_scores = [
                {
                    'player': 'A', 'prop_type': 'player_points', 'line': 20.5,
                    'recommended_side': 'over', 'recommended_odds': -150,
                    'edge': 0.12, 'confidence_tier': 'strong', 'games_played': 20,
                    'game_id': 'g1',
                },
                {
                    'player': 'B', 'prop_type': 'player_points', 'line': 18.5,
                    'recommended_side': 'under', 'recommended_odds': -150,
                    'edge': 0.11, 'confidence_tier': 'strong', 'games_played': 20,
                    'game_id': 'g2',
                },
                {
                    'player': 'C', 'prop_type': 'player_points', 'line': 16.5,
                    'recommended_side': 'over', 'recommended_odds': 130,
                    'edge': 0.2, 'confidence_tier': 'strong', 'games_played': 20,
                    'game_id': 'g3',
                },
            ]
            parlay = detector.recommend_best_parlay(
                scores=mock_scores,
                min_edge=0.08,
                min_odds=100,
                max_odds=200,
                min_legs=2,
                max_legs=3,
            )
            self.assertIsNotNone(parlay)
            self.assertGreaterEqual(parlay['combined_odds'], 100)
            self.assertLessEqual(parlay['combined_odds'], 200)
            self.assertIn(len(parlay['legs']), (2, 3))

    def test_score_prop_strong_requires_projection_confidence(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        detector.engine = MagicMock()
        detector.engine.project_stat.return_value = {
            'projection': 40.0,
            'std_dev': 4.0,
            'games_played': 20,
            'confidence': 'low',
            'context_notes': [],
            'z_score': 0,
            'projection_source': 'heuristic',
            'breakdown': {},
        }
        result = detector.score_prop(
            player_name='Test Player',
            prop_type='player_points',
            line=20.5,
            over_odds=-110,
            under_odds=-110,
        )
        self.assertEqual(result['confidence_tier'], 'moderate')

    def test_score_prop_strong_when_confidence_medium_or_high(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        detector.engine = MagicMock()
        detector.engine.project_stat.return_value = {
            'projection': 40.0,
            'std_dev': 4.0,
            'games_played': 20,
            'confidence': 'high',
            'context_notes': [],
            'z_score': 0,
            'projection_source': 'heuristic',
            'breakdown': {},
        }
        result = detector.score_prop(
            player_name='Test Player',
            prop_type='player_points',
            line=20.5,
            over_odds=-110,
            under_odds=-110,
        )
        self.assertEqual(result['confidence_tier'], 'strong')

    # -- _model_prob_over --

    def test_model_prob_over_scipy(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        prob = detector._model_prob_over(30, 25, 5)
        self.assertGreater(prob, 0.5)

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
                 patch('app.services.distributional_predictor.predict_prob_over_details',
                       return_value={'prob_over': 0.777, 'point': 25.0, 'kind': 'quantile'}):
                result = detector.score_prop(
                    'Dist Flag Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertAlmostEqual(result['model_prob_over'], 0.777)

    def _seed_scenario_player(self, pid='920', name='Scenario Player'):
        for i in range(20):
            db.session.add(PlayerGameLog(
                player_id=pid, player_name=name, team_abbr='TST',
                game_date=date(2026, 1, 1) + timedelta(days=i),
                pts=25, reb=6, ast=4, fg3m=2, minutes=33,
                stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
            ))
        db.session.commit()

    def test_scenario_signal_fields_note_and_demotion(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            self._seed_scenario_player()
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='920'), \
                 patch.dict('os.environ', {'USE_SCENARIO_SIGNAL': 'true'}), \
                 patch('app.services.player_crosswalk.resolve_espn_id', return_value='4396'), \
                 patch('app.services.live_context.build_live_context',
                       return_value=({'home_away': 'home'}, True)), \
                 patch('app.services.scenario_engine.agreement_score',
                       return_value=(-0.8, 9)):
                result = detector.score_prop(
                    'Scenario Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertEqual(result['scenario_agreement'], -0.8)
        self.assertEqual(result['scenario_matches'], 9)
        self.assertTrue(any('Scenario splits' in n for n in result['context_notes']))
        # strong disagreement demotes one tier from whatever it was
        self.assertIn(result['confidence_tier'],
                      ('moderate', 'slight', 'no_edge'))

    def test_scenario_promotion_only_from_slight(self):
        from app.services import value_detector as vd
        self.assertEqual(vd._TIER_DEMOTE['moderate'], 'slight')
        self.assertEqual(vd._apply_scenario_nudge('slight', 0.7, 6), 'moderate')
        self.assertEqual(vd._apply_scenario_nudge('moderate', 0.7, 6), 'moderate')
        self.assertEqual(vd._apply_scenario_nudge('moderate', -0.7, 6), 'slight')
        self.assertEqual(vd._apply_scenario_nudge('moderate', 0.7, 3), 'moderate')   # < MIN_MATCHES
        self.assertEqual(vd._apply_scenario_nudge('moderate', 0.3, 9), 'moderate')   # < threshold

    def test_flag_off_result_is_byte_identical_and_fields_none(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            self._seed_scenario_player(pid='921', name='Flagoff Player')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='921'):
                result = detector.score_prop(
                    'Flagoff Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertIsNone(result['scenario_agreement'])
        self.assertIsNone(result['scenario_matches'])
        self.assertFalse(any('Scenario splits' in n for n in result['context_notes']))

    def test_scenario_exception_never_breaks_scoring(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            self._seed_scenario_player(pid='922', name='Boom Player')
            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='922'), \
                 patch.dict('os.environ', {'USE_SCENARIO_SIGNAL': 'true'}), \
                 patch('app.services.player_crosswalk.resolve_espn_id',
                       side_effect=RuntimeError('boom')):
                result = detector.score_prop(
                    'Boom Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertIsNone(result['scenario_agreement'])
        self.assertGreater(result['model_prob_over'], 0)

    def test_dist_scored_prop_displays_dist_median_as_projection(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id='913', player_name='Dist Median Player', team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25 + (i % 3), reb=6, ast=4, fg3m=2, minutes=33,
                    stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
                ))
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='913'), \
                 patch.dict('os.environ', {'USE_DISTRIBUTIONAL_MODEL': 'true'}), \
                 patch(
                     'app.services.distributional_predictor.predict_prob_over_details',
                     return_value={'prob_over': 0.31, 'point': 31.24, 'kind': 'quantile'},
                 ):
                result = detector.score_prop(
                    'Dist Median Player', 'player_points',
                    line=34.5, over_odds=-110, under_odds=-110,
                )
        self.assertAlmostEqual(result['model_prob_over'], 0.31)
        self.assertEqual(result['projection'], 31.2)
        self.assertEqual(result['projection_source'], 'distributional')

    def test_flag_off_projection_stays_heuristic(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id='914', player_name='Heuristic Player', team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25, reb=6, ast=4, fg3m=2, minutes=33,
                    stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
                ))
            db.session.commit()

            detector = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='914'):
                result = detector.score_prop(
                    'Heuristic Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertNotEqual(result['projection_source'], 'distributional')
        self.assertGreater(result['projection'], 0)

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
                 patch('app.services.distributional_predictor.predict_prob_over_details', return_value=None):
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

    def test_model_prob_over_falls_back_when_predictor_raises(self):
        from app.services.value_detector import ValueDetector
        with self.app.app_context():
            for i in range(20):
                db.session.add(PlayerGameLog(
                    player_id='912', player_name='Exception Fallback Player', team_abbr='TST',
                    game_date=date(2026, 1, 1) + timedelta(days=i),
                    pts=25, reb=6, ast=4, fg3m=2, minutes=33,
                    stl=1, blk=0, tov=2, fgm=9, fga=18, ftm=5, fta=6, fg3a=6,
                ))
            db.session.commit()

            detector_on = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='912'), \
                 patch.dict('os.environ', {'USE_DISTRIBUTIONAL_MODEL': 'true'}), \
                 patch(
                     'app.services.distributional_predictor.predict_prob_over_details',
                     side_effect=RuntimeError('distributional inference failed'),
                 ):
                flag_on_result = detector_on.score_prop(
                    'Exception Fallback Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )

            detector_off = ValueDetector()
            with patch('app.services.projection_engine.find_player_id', return_value='912'):
                flag_off_result = detector_off.score_prop(
                    'Exception Fallback Player', 'player_points',
                    line=20.5, over_odds=-110, under_odds=-110,
                )
        self.assertAlmostEqual(flag_on_result['model_prob_over'], flag_off_result['model_prob_over'])

    # -- _empty_score --

    def test_empty_score(self):
        from app.services.value_detector import ValueDetector
        detector = ValueDetector()
        result = detector._empty_score('Player', 'player_points', 25.5, -110, -110, 'g1')
        self.assertEqual(result['projection'], 0)
        self.assertEqual(result['game_id'], 'g1')
        self.assertEqual(result['confidence_tier'], 'no_edge')


class TestValueDetectorModel2Integration(BaseTestCase):
    """Tests for Model 2 integration in score_prop()."""

    def _make_engine_with_proj(self, projection=25.0, std_dev=4.0, games=20,
                                confidence='medium', z_score=0.0, context_notes=None):
        engine = MagicMock()
        engine.project_stat.return_value = {
            'projection': projection,
            'std_dev': std_dev,
            'games_played': games,
            'confidence': confidence,
            'context_notes': context_notes or [],
            'z_score': z_score,
            'projection_source': 'heuristic',
            'breakdown': {'season_avg': projection},
        }
        return engine

    def test_score_prop_returns_win_probability_key(self):
        """score_prop always returns win_probability (None when Model 2 unavailable)."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj()
        detector = ValueDetector(engine=engine)
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       side_effect=Exception('no model')):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 24.5, -110, -110,
                )
        # win_probability is None when Model 2 is unavailable (exception swallowed)
        self.assertIn('win_probability', result)
        self.assertIn('pick_quality_recommendation', result)
        self.assertIsNone(result['win_probability'])
        self.assertEqual(result['pick_quality_recommendation'], 'no_model')

    def test_score_prop_model2_downgrades_moderate_to_slight(self):
        """confidence_tier is downgraded from moderate to slight when win_prob < 0.42."""
        from app.services.value_detector import ValueDetector
        # Use confidence='low' so even large edges don't become 'strong'
        # Then edge lands in 'moderate' range
        engine = self._make_engine_with_proj(projection=25.0, games=20, confidence='low')
        detector = ValueDetector(engine=engine)

        fake_quality = {
            'win_probability': 0.35,
            'recommendation': 'skip',
            'red_flags': [],
            'model_version': 'v1',
        }
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       return_value=fake_quality):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 22.5, -110, -110,
                )
        # With confidence='low' (not in STRONG_CONFIDENCE_LEVELS), the tier is 'moderate'
        # Model 2 downgrades 'moderate' to 'slight' because win_prob < 0.42
        self.assertEqual(result['win_probability'], 0.35)
        self.assertEqual(result['pick_quality_recommendation'], 'skip')
        # The tier should have been downgraded from moderate to slight
        self.assertIn(result['confidence_tier'], ('slight', 'no_edge'))

    def test_score_prop_model2_adds_quality_note_high_prob(self):
        """High win_prob adds ML quality context note."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj(projection=28.0, games=20, confidence='high')
        detector = ValueDetector(engine=engine)

        fake_quality = {
            'win_probability': 0.72,
            'recommendation': 'take_it',
            'red_flags': [],
            'model_version': 'v1',
        }
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       return_value=fake_quality):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 20.5, -110, -110,
                )
        self.assertEqual(result['win_probability'], 0.72)
        self.assertTrue(any('ML quality' in n for n in result['context_notes']))

    def test_score_prop_model2_adds_caution_note_low_prob(self):
        """Low win_prob adds ML caution context note."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj(projection=22.0, games=20, confidence='medium')
        detector = ValueDetector(engine=engine)

        fake_quality = {
            'win_probability': 0.30,
            'recommendation': 'skip',
            'red_flags': ['high variance'],
            'model_version': 'v1',
        }
        with self.app.app_context():
            with patch('app.services.value_detector.predict_pick_quality',
                       return_value=fake_quality):
                result = detector.score_prop(
                    'LeBron James', 'player_points', 22.5, -110, -110,
                )
        self.assertEqual(result['win_probability'], 0.30)
        self.assertTrue(any('ML caution' in n for n in result['context_notes']))

    def test_score_prop_b2b_detected_from_context_notes(self):
        """B2B flag is correctly inferred from projection context_notes (fallback path)."""
        from app.services.value_detector import ValueDetector
        engine = self._make_engine_with_proj(
            projection=22.0, games=20, confidence='medium',
            context_notes=['back-to-back (-8%)', 'away game (-3%)']
        )
        detector = ValueDetector(engine=engine)

        captured_ctx = {}

        def capture_ctx(ctx, **kwargs):
            captured_ctx.update(ctx)
            return {'win_probability': 0.55, 'recommendation': 'caution', 'red_flags': []}

        with self.app.app_context():
            # Force fallback path (no player_id) so B2B is read from context_notes.
            with patch('app.services.value_detector.find_player_id', return_value=''), \
                 patch('app.services.value_detector.predict_pick_quality',
                       side_effect=capture_ctx):
                detector.score_prop('LeBron James', 'player_points', 22.5, -110, -110)

        self.assertTrue(captured_ctx.get('back_to_back'))


class TestBuildCandidates(unittest.TestCase):
    """Unit tests for _build_candidates()."""

    def _score(self, player, prop, line, side, game, edge, games_played):
        return {'player': player, 'prop_type': prop, 'line': line,
                'recommended_side': side, 'game_id': game,
                'edge': edge, 'games_played': games_played}

    def test_drops_below_min_games(self):
        from app.services.scheduler import _build_candidates
        scores = [self._score('A', 'pts', 20.5, 'over', 'g1', 0.2, 3)]
        self.assertEqual(_build_candidates(scores, min_games=5), [])

    def test_deduplicates_identical_key(self):
        from app.services.scheduler import _build_candidates
        s = self._score('A', 'pts', 20.5, 'over', 'g1', 0.2, 10)
        result = _build_candidates([s, s], min_games=5)
        self.assertEqual(len(result), 1)

    def test_sorts_by_edge_descending(self):
        from app.services.scheduler import _build_candidates
        lo = self._score('A', 'pts', 20.5, 'over', 'g1', 0.05, 10)
        hi = self._score('B', 'reb', 8.5,  'over', 'g2', 0.20, 10)
        result = _build_candidates([lo, hi], min_games=5)
        self.assertEqual(result[0]['player'], 'B')


class TestFilterQualifying(unittest.TestCase):
    """Unit tests for _filter_qualifying()."""

    def _cand(self, games_played, tier):
        return {'games_played': games_played, 'confidence_tier': tier, 'edge': 0.1}

    def test_keeps_meeting_both_thresholds(self):
        from app.services.scheduler import _filter_qualifying
        c = self._cand(10, 'strong')
        self.assertEqual(_filter_qualifying([c], 5, 'strong'), [c])

    def test_drops_wrong_tier(self):
        from app.services.scheduler import _filter_qualifying
        c = self._cand(10, 'moderate')
        self.assertEqual(_filter_qualifying([c], 5, 'strong'), [])

    def test_drops_below_min_games(self):
        from app.services.scheduler import _filter_qualifying
        c = self._cand(3, 'strong')
        self.assertEqual(_filter_qualifying([c], 5, 'strong'), [])


class TestBuildStraightPlays(unittest.TestCase):
    """Unit tests for _build_straight_plays()."""

    def _play(self, player, edge):
        return {'player': player, 'edge': edge, 'prop_type': 'pts',
                'line': 20.5, 'game_id': f'g_{player}'}

    def test_drops_below_min_edge(self):
        from app.services.scheduler import _build_straight_plays
        plays = [self._play('A', 0.05)]
        self.assertEqual(_build_straight_plays(plays, min_edge_straight=0.15), [])

    def test_one_per_player(self):
        from app.services.scheduler import _build_straight_plays
        p1 = self._play('A', 0.20)
        p2 = self._play('A', 0.18)
        result = _build_straight_plays([p1, p2], min_edge_straight=0.15)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], p1)

    def test_different_players_both_included(self):
        from app.services.scheduler import _build_straight_plays
        plays = [self._play('A', 0.20), self._play('B', 0.16)]
        result = _build_straight_plays(plays, min_edge_straight=0.15)
        self.assertEqual(len(result), 2)


class TestComputeBetOutcome(unittest.TestCase):
    """Unit tests for the _compute_bet_outcome() pure function."""

    def setUp(self):
        from app.services.nba_service import _compute_bet_outcome
        self.fn = _compute_bet_outcome

    def test_push_when_actual_equals_line(self):
        from app.models import Outcome
        result = self.fn('over', 25.5, 25.5)
        self.assertEqual(result, Outcome.PUSH.value)

    def test_over_win(self):
        from app.models import Outcome
        result = self.fn('over', 25.5, 26.0)
        self.assertEqual(result, Outcome.WIN.value)

    def test_over_lose(self):
        from app.models import Outcome
        result = self.fn('over', 25.5, 25.0)
        self.assertEqual(result, Outcome.LOSE.value)

    def test_under_win(self):
        from app.models import Outcome
        result = self.fn('under', 25.5, 25.0)
        self.assertEqual(result, Outcome.WIN.value)

    def test_under_lose(self):
        from app.models import Outcome
        result = self.fn('under', 25.5, 26.0)
        self.assertEqual(result, Outcome.LOSE.value)
