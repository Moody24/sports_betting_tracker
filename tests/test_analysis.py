"""Tests for the analysis engine, value detector, and supporting services."""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

from tests.helpers import BaseTestCase, make_user, make_bet
from app import db
from app.models import (
    PlayerGameLog, TeamDefenseSnapshot, InjuryReport,
    PickContext, ModelMetadata, JobLog,
)


class TestNewModels(BaseTestCase):
    """Verify new model creation and basic relationships."""

    def test_player_game_log_creation(self):
        with self.app.app_context():
            log = PlayerGameLog(
                player_id='12345',
                player_name='Test Player',
                team_abbr='TST',
                game_date=date(2026, 2, 20),
                pts=25.0,
                reb=8.0,
                ast=6.0,
                fg3m=3.0,
                minutes=34.5,
            )
            db.session.add(log)
            db.session.commit()

            fetched = PlayerGameLog.query.filter_by(player_id='12345').first()
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.player_name, 'Test Player')
            self.assertEqual(fetched.pts, 25.0)
            self.assertEqual(fetched.reb, 8.0)

    def test_player_game_log_unique_constraint(self):
        """Duplicate player_id + game_date should fail."""
        with self.app.app_context():
            log1 = PlayerGameLog(
                player_id='111', player_name='Player A',
                game_date=date(2026, 2, 20), pts=20,
            )
            log2 = PlayerGameLog(
                player_id='111', player_name='Player A',
                game_date=date(2026, 2, 20), pts=25,
            )
            db.session.add(log1)
            db.session.commit()
            db.session.add(log2)
            with self.assertRaises(Exception):
                db.session.commit()
            db.session.rollback()

    def test_team_defense_snapshot_creation(self):
        with self.app.app_context():
            snap = TeamDefenseSnapshot(
                team_id='100',
                team_name='Test Team',
                team_abbr='TST',
                snapshot_date=date(2026, 2, 25),
                opp_pts_pg=112.5,
                pace=101.2,
                def_rating=108.3,
            )
            db.session.add(snap)
            db.session.commit()

            fetched = TeamDefenseSnapshot.query.first()
            self.assertEqual(fetched.team_name, 'Test Team')
            self.assertAlmostEqual(fetched.opp_pts_pg, 112.5)

    def test_injury_report_creation(self):
        with self.app.app_context():
            report = InjuryReport(
                player_name='Test Player',
                team='Test Team',
                status='questionable',
                detail='Knee soreness',
                date_reported=date(2026, 2, 25),
            )
            db.session.add(report)
            db.session.commit()

            fetched = InjuryReport.query.first()
            self.assertEqual(fetched.status, 'questionable')

    def test_pick_context_creation(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()

            bet_obj = make_bet(user.id)
            db.session.add(bet_obj)
            db.session.commit()

            ctx = PickContext(
                bet_id=bet_obj.id,
                context_json='{"projected_stat": 27.3, "edge": 0.18}',
                projected_stat=27.3,
                projected_edge=0.18,
                confidence_tier='strong',
            )
            db.session.add(ctx)
            db.session.commit()

            fetched = PickContext.query.first()
            self.assertEqual(fetched.projected_stat, 27.3)
            self.assertEqual(fetched.context['projected_stat'], 27.3)

            # Verify backref
            self.assertIsNotNone(bet_obj.pick_context)

    def test_model_metadata_creation(self):
        with self.app.app_context():
            meta = ModelMetadata(
                model_name='projection_points',
                model_type='xgboost_regressor',
                version='test_v1',
                file_path='/tmp/test_model.json',
                training_date=datetime.now(timezone.utc),
                training_samples=1000,
                val_mae=2.5,
                is_active=True,
            )
            db.session.add(meta)
            db.session.commit()

            fetched = ModelMetadata.query.first()
            self.assertEqual(fetched.model_name, 'projection_points')
            self.assertTrue(fetched.is_active)

    def test_job_log_creation(self):
        with self.app.app_context():
            log = JobLog(
                job_name='stats_refresh',
                started_at=datetime.now(timezone.utc),
                status='success',
                message='Refreshed 150 players',
            )
            db.session.add(log)
            db.session.commit()

            fetched = JobLog.query.first()
            self.assertEqual(fetched.job_name, 'stats_refresh')


class TestPlayerNameResolver(BaseTestCase):
    """Test fuzzy name matching."""

    def test_exact_match(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        result = resolver.best_match('LeBron James', ['LeBron James', 'Kevin Durant'])
        self.assertEqual(result, 'LeBron James')

    def test_substring_match(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        result = resolver.best_match('LeBron', ['LeBron James', 'Kevin Durant'])
        self.assertEqual(result, 'LeBron James')

    def test_fuzzy_match(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        result = resolver.best_match('Lebron Jams', ['LeBron James', 'Kevin Durant'])
        self.assertEqual(result, 'LeBron James')

    def test_no_match_below_threshold(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        result = resolver.best_match('XYZ', ['LeBron James', 'Kevin Durant'], threshold=0.9)
        self.assertIsNone(result)

    def test_empty_candidates(self):
        from app.services.stats_service import PlayerNameResolver
        resolver = PlayerNameResolver()
        result = resolver.best_match('LeBron', [])
        self.assertIsNone(result)


class TestValueDetector(BaseTestCase):
    """Test edge calculation and implied probability."""

    def test_implied_prob_positive_odds(self):
        from app.services.value_detector import implied_prob
        # +150 -> 100/250 = 0.4
        self.assertAlmostEqual(implied_prob(150), 0.4, places=2)

    def test_implied_prob_negative_odds(self):
        from app.services.value_detector import implied_prob
        # -110 -> 110/210 ≈ 0.524
        self.assertAlmostEqual(implied_prob(-110), 110 / 210, places=3)

    def test_implied_prob_zero(self):
        from app.services.value_detector import implied_prob
        self.assertEqual(implied_prob(0), 0.5)

    def test_decimal_odds_positive(self):
        from app.services.value_detector import decimal_odds
        # +200 -> 3.0
        self.assertAlmostEqual(decimal_odds(200), 3.0)

    def test_decimal_odds_negative(self):
        from app.services.value_detector import decimal_odds
        # -200 -> 1.5
        self.assertAlmostEqual(decimal_odds(-200), 1.5)

    def test_quarter_kelly_positive_edge(self):
        from app.services.value_detector import quarter_kelly
        stake = quarter_kelly(edge=0.10, american_odds=-110, bankroll=1000)
        self.assertGreater(stake, 0)
        self.assertLessEqual(stake, 50)  # 5% cap on $1000

    def test_quarter_kelly_no_edge(self):
        from app.services.value_detector import quarter_kelly
        stake = quarter_kelly(edge=0, american_odds=-110, bankroll=1000)
        self.assertEqual(stake, 0)

    def test_quarter_kelly_negative_edge(self):
        from app.services.value_detector import quarter_kelly
        stake = quarter_kelly(edge=-0.05, american_odds=-110, bankroll=1000)
        self.assertEqual(stake, 0)

    def test_quarter_kelly_cap(self):
        from app.services.value_detector import quarter_kelly
        # With a huge edge, should be capped at 5% of bankroll
        stake = quarter_kelly(edge=0.50, american_odds=+300, bankroll=1000)
        self.assertLessEqual(stake, 50.0)


class TestProjectionEngine(BaseTestCase):
    """Test projection engine with mocked data."""

    def _seed_logs(self, player_id='999', count=15):
        """Create fake game logs in the database."""
        for i in range(count):
            log = PlayerGameLog(
                player_id=player_id,
                player_name='Test Player',
                team_abbr='TST',
                game_date=date(2026, 2, 1 + i) if i < 28 else date(2026, 1, i - 27),
                pts=20 + i * 0.5,
                reb=7.0,
                ast=5.0,
                fg3m=2.0,
                minutes=32.0,
                home_away='home' if i % 2 == 0 else 'away',
            )
            db.session.add(log)
        db.session.commit()

    def test_get_player_stats_summary(self):
        from app.services.stats_service import get_player_stats_summary, get_cached_logs
        with self.app.app_context():
            self._seed_logs()
            logs = get_cached_logs('999')
            summary = get_player_stats_summary('999', logs)

            self.assertIn('last_5', summary)
            self.assertIn('last_10', summary)
            self.assertIn('season', summary)
            self.assertEqual(summary['games_played'], 15)
            self.assertGreater(summary['last_5']['pts'], 0)

    def test_empty_projection(self):
        from app.services.projection_engine import ProjectionEngine
        with self.app.app_context():
            engine = ProjectionEngine()
            # Non-existent player returns empty projection
            with patch('app.services.projection_engine.find_player_id', return_value=None):
                result = engine.project_stat('Nobody', 'player_points')
                self.assertEqual(result['projection'], 0)
                self.assertEqual(result['confidence'], 'low')


class TestMatchupService(BaseTestCase):
    """Test defensive matchup calculations."""

    def _seed_defense(self):
        snap = TeamDefenseSnapshot(
            team_id='1',
            team_name='Boston Celtics',
            team_abbr='BOS',
            snapshot_date=date(2026, 2, 25),
            opp_pts_pg=108.0,
            opp_reb_pg=42.0,
            opp_ast_pg=24.0,
            opp_3pm_pg=11.0,
            pace=98.5,
            def_rating=106.5,
        )
        db.session.add(snap)
        db.session.commit()

    def test_get_team_defense(self):
        from app.services.matchup_service import get_team_defense
        with self.app.app_context():
            self._seed_defense()
            defense = get_team_defense('Celtics')
            self.assertEqual(defense['team_name'], 'Boston Celtics')
            self.assertAlmostEqual(defense['opp_pts_pg'], 108.0)

    def test_get_team_defense_not_found(self):
        from app.services.matchup_service import get_team_defense
        with self.app.app_context():
            result = get_team_defense('Nonexistent Team')
            self.assertEqual(result, {})

    def test_matchup_adjustment(self):
        from app.services.matchup_service import get_matchup_adjustment
        with self.app.app_context():
            self._seed_defense()
            # Celtics allow 108 pts vs 114 league avg -> < 1.0 (good defense)
            adj = get_matchup_adjustment('Celtics', 'player_points')
            self.assertLess(adj, 1.0)

    def test_pace_factor(self):
        from app.services.matchup_service import get_pace_factor
        with self.app.app_context():
            self._seed_defense()
            # Celtics pace 98.5 vs 100 avg -> < 1.0 (slower)
            pace = get_pace_factor('Celtics')
            self.assertLess(pace, 1.0)


class TestContextService(BaseTestCase):
    """Test injury and context lookups."""

    def test_injury_status_lookup(self):
        from app.services.context_service import get_player_injury_status
        with self.app.app_context():
            report = InjuryReport(
                player_name='Test Player',
                team='Test Team',
                status='questionable',
                detail='Ankle sprain',
                date_reported=date(2026, 2, 25),
            )
            db.session.add(report)
            db.session.commit()

            status = get_player_injury_status('Test Player')
            self.assertEqual(status['status'], 'questionable')

    def test_player_available_when_healthy(self):
        from app.services.context_service import is_player_available
        with self.app.app_context():
            # No injury report = available
            self.assertTrue(is_player_available('Healthy Player'))

    def test_player_not_available_when_out(self):
        from app.services.context_service import is_player_available
        with self.app.app_context():
            report = InjuryReport(
                player_name='Hurt Player',
                team='Test Team',
                status='out',
                date_reported=date(2026, 2, 25),
            )
            db.session.add(report)
            db.session.commit()

            self.assertFalse(is_player_available('Hurt Player'))

    def test_normalize_injury_status(self):
        from app.services.context_service import _normalize_injury_status
        self.assertEqual(_normalize_injury_status('Out'), 'out')
        self.assertEqual(_normalize_injury_status('Doubtful'), 'doubtful')
        self.assertEqual(_normalize_injury_status('Day-To-Day'), 'day-to-day')
        self.assertEqual(_normalize_injury_status('Probable'), 'probable')
        self.assertEqual(_normalize_injury_status('Questionable'), 'questionable')


class TestAnalysisRoute(BaseTestCase):
    """Test the analysis page route."""

    def test_analysis_page_requires_login(self):
        resp = self.client.get('/nba/analysis')
        self.assertEqual(resp.status_code, 302)

    def test_analysis_page_loads(self):
        self.register_and_login()
        with patch('app.routes.bet.ValueDetector') as mock_vd:
            mock_instance = MagicMock()
            mock_instance.score_all_todays_props.return_value = []
            mock_instance.filter_plays.return_value = []
            mock_vd.return_value = mock_instance

            resp = self.client.get('/nba/analysis')
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'NBA Prop Analysis', resp.data)

    def test_analysis_counts_from_full_filtered_set(self):
        self.register_and_login()
        def _play(i, tier):
            return {
                'player': f'Player {i}',
                'prop_type': 'player_points',
                'line': 20.5,
                'projection': 25.2,
                'edge': 0.2 if tier == 'strong' else 0.09,
                'recommended_side': 'over',
                'recommended_odds': -110,
                'confidence_tier': tier,
                'confidence': 'high',
                'context_notes': [],
                'game_id': f'g{i}',
                'home_team': 'Lakers',
                'away_team': 'Celtics',
                'match_date': '2026-02-27',
            }

        full_filtered = [_play(1, 'strong'), _play(2, 'strong'), _play(2, 'moderate')]
        full_filtered.extend(_play(i, 'strong') for i in range(3, 61))

        with patch('app.routes.bet.ValueDetector') as mock_vd:
            mock_instance = MagicMock()
            mock_instance.score_all_todays_props.return_value = ['raw']
            mock_instance.filter_plays.return_value = full_filtered
            mock_vd.return_value = mock_instance

            resp = self.client.get('/nba/analysis')
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b'Value Plays Found', resp.data)
            self.assertIn(b'>60<', resp.data)
            self.assertIn(b'>1<', resp.data)
            self.assertIn(b'top 50 shown', resp.data)


class TestFeatureEngine(BaseTestCase):
    """Test feature engineering functions."""

    def test_prop_to_stat_key(self):
        from app.services.feature_engine import _prop_to_stat_key
        self.assertEqual(_prop_to_stat_key('player_points'), 'pts')
        self.assertEqual(_prop_to_stat_key('player_rebounds'), 'reb')
        self.assertEqual(_prop_to_stat_key('player_assists'), 'ast')
        self.assertEqual(_prop_to_stat_key('player_threes'), 'fg3m')
        self.assertEqual(_prop_to_stat_key('unknown'), 'pts')

    def test_compute_hit_rate(self):
        from app.services.feature_engine import _compute_hit_rate
        with self.app.app_context():
            # Create mock logs
            logs = []
            for i in range(10):
                log = PlayerGameLog(
                    player_id='test',
                    player_name='Test',
                    game_date=date(2026, 2, 1 + i),
                    pts=20 + i,
                )
                logs.append(log)

            # Line of 24.5: games with pts > 24.5 are indices 5-9 (5 games)
            rate = _compute_hit_rate(logs, 'pts', 24.5)
            self.assertAlmostEqual(rate, 0.5)


if __name__ == '__main__':
    unittest.main()
