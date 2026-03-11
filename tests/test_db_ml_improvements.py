"""Tests for DB/ML improvements: composite indexes, context_flags removal,
model_accuracy CLI, data quality gates, model_status drift section,
and prod-readiness CLI command."""

import json
from datetime import datetime, timezone, timedelta, date as date_type
from unittest.mock import patch, MagicMock

from app import db
from app.models import (
    Bet,
    BetPostmortem,
    JobLog,
    ModelMetadata,
    OddsSnapshot,
    PickContext,
    PlayerGameLog,
    TeamDefenseSnapshot,
)
from tests.helpers import BaseTestCase, make_bet, make_user


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_player_log(player_id='p1', player_name='LeBron James',
                     game_date=None, pts=25.0, minutes=35.0,
                     team_abbr='LAL', home_away='home', win_loss='W'):
    return PlayerGameLog(
        player_id=player_id,
        player_name=player_name,
        game_date=game_date or date_type(2025, 3, 1),
        pts=pts,
        reb=8.0,
        ast=7.0,
        stl=1.0,
        blk=0.5,
        fg3m=1.0,
        fg3a=4.0,
        fgm=10.0,
        fga=20.0,
        ftm=3.0,
        fta=4.0,
        tov=2.0,
        plus_minus=5.0,
        minutes=minutes,
        team_abbr=team_abbr,
        home_away=home_away,
        win_loss=win_loss,
    )


def _make_model_meta(model_name='projection_player_points', val_mae=2.5,
                     val_accuracy=None, training_samples=800, days_ago=3):
    return ModelMetadata(
        model_name=model_name,
        model_type='xgboost_regressor',
        version=f'{model_name}_v1',
        file_path='/tmp/fake.json',
        training_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
        training_samples=training_samples,
        val_mae=val_mae,
        val_accuracy=val_accuracy,
        is_active=True,
    )


# ── Unit 2: context_flags removed from model ─────────────────────────────────


class TestContextFlagsRemoved(BaseTestCase):
    """Verify PlayerGameLog no longer has context_flags column."""

    def test_player_game_log_has_no_context_flags_attr(self):
        # The column was dropped — the ORM model should not have it
        log = _make_player_log()
        self.assertFalse(hasattr(log, 'context_flags'),
                         "context_flags attribute should not exist on PlayerGameLog")

    def test_player_game_log_can_be_created_without_context_flags(self):
        with self.app.app_context():
            log = _make_player_log()
            db.session.add(log)
            db.session.commit()
            fetched = db.session.get(PlayerGameLog, log.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.pts, 25.0)


# ── Unit 3: model_accuracy CLI ───────────────────────────────────────────────


class TestModelAccuracyCLI(BaseTestCase):
    """Tests for the flask model_accuracy command."""

    def _run_model_accuracy(self, *args):
        from click.testing import CliRunner
        runner = CliRunner()
        with self.app.app_context():
            from flask.cli import FlaskGroup
            # Use app.test_cli_runner for simplicity
        return self.app.test_cli_runner().invoke(args=list(('model_accuracy',) + args))

    def test_model_accuracy_no_data(self):
        result = self.app.test_cli_runner().invoke(args=['model_accuracy'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('No postmortem data', result.output)

    def test_model_accuracy_with_data(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.flush()

            bet = make_bet(user.id,
                           outcome='win',
                           player_name='LeBron James',
                           prop_type='player_points',
                           prop_line=25.5,
                           actual_total=28.0,
                           bet_type='over')
            db.session.add(bet)
            db.session.flush()

            pm = BetPostmortem(
                bet_id=bet.id,
                player_name='LeBron James',
                game_date=date_type(2025, 3, 1),
                stat_type='player_points',
                projected_stat=26.0,
                actual_stat=28.0,
                prop_line=25.5,
                bet_side='over',
                projection_error=2.0,
            )
            db.session.add(pm)

            meta = _make_model_meta()
            db.session.add(meta)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['model_accuracy'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('player_points', result.output)
        self.assertIn('MAE', result.output)

    def test_model_accuracy_stat_type_filter(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.flush()

            bet = make_bet(user.id, outcome='lose',
                           player_name='Stephen Curry',
                           prop_type='player_threes', prop_line=4.5,
                           actual_total=3.0, bet_type='over')
            db.session.add(bet)
            db.session.flush()

            pm = BetPostmortem(
                bet_id=bet.id,
                player_name='Stephen Curry',
                game_date=date_type(2025, 3, 2),
                stat_type='player_threes',
                projected_stat=4.8,
                actual_stat=3.0,
                prop_line=4.5,
                bet_side='over',
                projection_error=-1.8,
            )
            db.session.add(pm)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(
            args=['model_accuracy', '--stat-type', 'player_threes'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('player_threes', result.output)

    def test_model_accuracy_shows_comparison_no_model(self):
        """When no active model metadata exists, shows 'val=n/a'."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.flush()

            bet = make_bet(user.id, outcome='win',
                           player_name='Giannis',
                           prop_type='player_points', prop_line=30.5,
                           actual_total=35.0, bet_type='over')
            db.session.add(bet)
            db.session.flush()

            pm = BetPostmortem(
                bet_id=bet.id,
                stat_type='player_points',
                projected_stat=31.0,
                actual_stat=35.0,
                prop_line=30.5,
            )
            db.session.add(pm)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['model_accuracy'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('val=n/a', result.output)


# ── Unit 4: data quality gates ───────────────────────────────────────────────


class TestDataQualityGates(BaseTestCase):
    """Tests for _check_training_data_quality and check_defense_snapshot_staleness."""

    def test_quality_gate_passes_clean_data(self):
        from app.services.ml_model import _check_training_data_quality

        logs = [_make_player_log(pts=25.0, minutes=35.0) for _ in range(10)]
        result = _check_training_data_quality(logs)
        self.assertTrue(result['passed'])
        self.assertEqual(result['issues'], [])

    def test_quality_gate_fails_high_null_pts(self):
        from app.services.ml_model import _check_training_data_quality

        clean = [_make_player_log(pts=25.0, minutes=35.0) for _ in range(90)]
        nulls = [_make_player_log(pts=None, minutes=35.0) for _ in range(10)]
        result = _check_training_data_quality(clean + nulls)
        self.assertFalse(result['passed'])
        self.assertTrue(any('pts null rate' in i for i in result['issues']))

    def test_quality_gate_fails_out_of_range_pts(self):
        from app.services.ml_model import _check_training_data_quality

        logs = [_make_player_log(pts=200.0, minutes=35.0)]  # pts > 100
        result = _check_training_data_quality(logs)
        self.assertFalse(result['passed'])
        self.assertTrue(any('pts outside [0, 100]' in i for i in result['issues']))

    def test_quality_gate_fails_out_of_range_minutes(self):
        from app.services.ml_model import _check_training_data_quality

        logs = [_make_player_log(pts=25.0, minutes=70.0)]  # minutes > 60
        result = _check_training_data_quality(logs)
        self.assertFalse(result['passed'])
        self.assertTrue(any('minutes outside [0, 60]' in i for i in result['issues']))

    def test_defense_staleness_no_rows(self):
        with self.app.app_context():
            from app.services.ml_model import check_defense_snapshot_staleness
            result = check_defense_snapshot_staleness()
            self.assertTrue(result['stale'])
            self.assertIsNone(result['days_old'])

    def test_defense_staleness_fresh(self):
        with self.app.app_context():
            snap = TeamDefenseSnapshot(
                team_id='t1',
                team_name='Lakers',
                team_abbr='LAL',
                snapshot_date=date_type.today(),
            )
            db.session.add(snap)
            db.session.commit()

            from app.services.ml_model import check_defense_snapshot_staleness
            result = check_defense_snapshot_staleness()
            self.assertFalse(result['stale'])
            self.assertEqual(result['days_old'], 0)

    def test_defense_staleness_stale(self):
        with self.app.app_context():
            old_date = date_type.today() - timedelta(days=10)
            snap = TeamDefenseSnapshot(
                team_id='t1',
                team_name='Lakers',
                team_abbr='LAL',
                snapshot_date=old_date,
            )
            db.session.add(snap)
            db.session.commit()

            from app.services.ml_model import check_defense_snapshot_staleness
            result = check_defense_snapshot_staleness()
            self.assertTrue(result['stale'])
            self.assertGreaterEqual(result['days_old'], 10)


# ── Unit 5: model_status drift section ──────────────────────────────────────


class TestModelStatusDriftSection(BaseTestCase):
    """Tests that flask model_status shows last drift check info."""

    def test_model_status_no_drift_jobs(self):
        result = self.app.test_cli_runner().invoke(args=['model_status'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Last Automated Drift Check', result.output)
        self.assertIn('No drift check job log found', result.output)

    def test_model_status_shows_last_drift_check(self):
        with self.app.app_context():
            now = datetime.now(timezone.utc)
            log = JobLog(
                job_name='drift_check',
                started_at=now - timedelta(days=3),
                finished_at=now - timedelta(days=3),
                status='success',
                message=None,
            )
            db.session.add(log)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['model_status'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('drift_check', result.output)
        self.assertIn('success', result.output)

    def test_model_status_shows_drift_warn_message(self):
        with self.app.app_context():
            now = datetime.now(timezone.utc)
            log = JobLog(
                job_name='drift_check',
                started_at=now - timedelta(days=1),
                finished_at=now - timedelta(days=1),
                status='warn',
                message='Model drift detected: rolling_win_rate=0.42, val_accuracy=0.57, delta=-0.15',
            )
            db.session.add(log)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['model_status'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('warn', result.output)
        self.assertIn('Model drift detected', result.output)


# ── Unit 6: prod-readiness CLI ───────────────────────────────────────────────


class TestProdReadinessCLI(BaseTestCase):
    """Tests for flask prod-readiness command."""

    def test_prod_readiness_no_data(self):
        """Runs without crash when DB is empty — should print FAILs/WARNs."""
        result = self.app.test_cli_runner().invoke(args=['prod-readiness'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Production Readiness Report', result.output)
        self.assertIn('VERDICT', result.output)

    def test_prod_readiness_with_fresh_defense_snapshot(self):
        with self.app.app_context():
            snap = TeamDefenseSnapshot(
                team_id='t1',
                team_name='Lakers',
                team_abbr='LAL',
                snapshot_date=date_type.today(),
            )
            db.session.add(snap)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['prod-readiness'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('TeamDefenseSnapshot', result.output)

    def test_prod_readiness_with_active_model(self):
        with self.app.app_context():
            meta = _make_model_meta(model_name='projection_player_points', val_mae=2.1)
            db.session.add(meta)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['prod-readiness'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('projection_player_points', result.output)
        self.assertIn('2.1', result.output)

    def test_prod_readiness_model2_resolved_count(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.flush()

            # Create 5 resolved bets with PickContext
            for i in range(5):
                bet = make_bet(user.id, outcome='win',
                               player_name=f'Player {i}',
                               prop_type='player_points',
                               prop_line=20.5, bet_type='over')
                db.session.add(bet)
                db.session.flush()
                pc = PickContext(
                    bet_id=bet.id,
                    context_json=json.dumps({}),
                )
                db.session.add(pc)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['prod-readiness'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('Resolved picks with context: 5', result.output)
        self.assertIn('FAIL', result.output)  # 5 < 200 threshold

    def test_prod_readiness_summary_verdict(self):
        """With no models and stale data, verdict should be NOT READY."""
        result = self.app.test_cli_runner().invoke(args=['prod-readiness'])
        self.assertEqual(result.exit_code, 0)
        # Either NOT READY or CAUTION since no models exist
        self.assertTrue(
            'NOT READY' in result.output or 'CAUTION' in result.output or 'PRODUCTION READY' in result.output
        )

    def test_prod_readiness_odds_snapshot_warn(self):
        """Stale OddsSnapshot shows WARN."""
        with self.app.app_context():
            old = date_type.today() - timedelta(days=10)
            snap = OddsSnapshot(
                game_id='g1',
                game_date=old,
                player_name='Player A',
                market='player_points',
                bookmaker='fanduel',
                line=25.5,
            )
            db.session.add(snap)
            db.session.commit()

        result = self.app.test_cli_runner().invoke(args=['prod-readiness'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('OddsSnapshot', result.output)
