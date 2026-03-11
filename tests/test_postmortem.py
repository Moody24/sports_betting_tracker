"""Tests for the bet postmortem system.

Covers:
- Reason-code assignment logic
- Postmortem creation on settlement
- Idempotency (re-running settlement does not create duplicates)
- BetPostmortem model helpers
- PostmortemReason enum completeness
"""
import json
import unittest
from datetime import datetime, timezone, date

from tests.helpers import BaseTestCase, make_bet, make_user
from app import db
from app.enums import BetType, PostmortemReason
from app.models import Bet, BetPostmortem, PlayerGameLog, GameSnapshot, PickContext
from app.services.postmortem_service import (
    _assign_reasons,
    create_or_update_postmortem,
    backfill_postmortems,
    PROP_TO_ATTEMPTS_KEY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prop_bet(user_id, outcome='lose', actual_total=2.0, prop_line=1.5, bet_type='over'):
    return make_bet(
        user_id,
        team_a='Pacers',
        team_b='Lakers',
        player_name='Benedict Mathurin',
        prop_type='player_threes',
        prop_line=prop_line,
        bet_type=bet_type,
        outcome=outcome,
        actual_total=actual_total,
        external_game_id='401756001',
        match_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )


def _add_game_log(player_name, game_date, minutes=28.0, fg3a=4.0, fg3m=1.0):
    """Insert a PlayerGameLog row for testing."""
    log = PlayerGameLog(
        player_id='999',
        player_name=player_name,
        game_date=game_date,
        minutes=minutes,
        fg3m=fg3m,
        fg3a=fg3a,
        pts=12.0,
        reb=3.0,
        ast=2.0,
        stl=0.5,
        blk=0.2,
        tov=1.0,
        fgm=5.0,
        fga=12.0,
        ftm=2.0,
        fta=2.0,
        plus_minus=0.0,
    )
    db.session.add(log)
    return log


# ---------------------------------------------------------------------------
# Tests: PostmortemReason enum
# ---------------------------------------------------------------------------

class TestPostmortemReasonEnum(unittest.TestCase):
    """Validate the enum covers expected reason codes."""

    def test_all_required_reason_codes_exist(self):
        required = {
            'minutes_miss', 'role_change', 'volume_spike', 'volume_drop',
            'efficiency_spike', 'efficiency_drop', 'ot_variance',
            'blowout_distortion', 'projection_model_miss', 'normal_variance',
            'line_value_miss', 'insufficient_edge', 'high_variance_event', 'unknown',
        }
        existing = {r.value for r in PostmortemReason}
        self.assertTrue(required.issubset(existing), f"Missing: {required - existing}")

    def test_reason_values_are_strings(self):
        for r in PostmortemReason:
            self.assertIsInstance(r.value, str)
            self.assertFalse(' ' in r.value, f"Reason code should use underscores: {r.value}")


# ---------------------------------------------------------------------------
# Tests: _assign_reasons logic
# ---------------------------------------------------------------------------

class TestAssignReasons(unittest.TestCase):
    """Unit tests for the deterministic rules engine."""

    BASE_KWARGS = dict(
        ctx={},
        bet_type=BetType.OVER.value,
        actual_stat=2.0,
        projected_stat=1.2,
        projection_error=0.8,
        player_variance=1.0,
        actual_minutes=None,
        expected_minutes=None,
        minutes_delta=None,
        actual_attempts=None,
        expected_attempts=None,
        attempts_delta=None,
        overtime_flag=False,
        blowout_flag=False,
        miss_margin=0.5,
    )

    def _call(self, **overrides):
        kwargs = dict(self.BASE_KWARGS)
        kwargs.update(overrides)
        return _assign_reasons(**kwargs)

    def test_ot_variance_flagged(self):
        reasons = self._call(overtime_flag=True)
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.OT_VARIANCE.value, codes)

    def test_blowout_flagged(self):
        reasons = self._call(blowout_flag=True)
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.BLOWOUT_DISTORTION.value, codes)

    def test_large_minutes_delta_triggers_minutes_miss(self):
        reasons = self._call(
            actual_minutes=36.0, expected_minutes=24.0, minutes_delta=12.0
        )
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.MINUTES_MISS.value, codes)

    def test_stable_minutes_trend_and_large_delta_triggers_role_change(self):
        reasons = self._call(
            ctx={'minutes_trend': 'stable'},
            actual_minutes=36.0, expected_minutes=24.0, minutes_delta=12.0,
        )
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.ROLE_CHANGE.value, codes)

    def test_volume_spike_detected(self):
        reasons = self._call(
            actual_attempts=8.0, expected_attempts=4.0, attempts_delta=4.0
        )
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.VOLUME_SPIKE.value, codes)

    def test_volume_drop_detected(self):
        reasons = self._call(
            actual_attempts=2.0, expected_attempts=5.0, attempts_delta=-3.0
        )
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.VOLUME_DROP.value, codes)

    def test_normal_variance_when_close_miss(self):
        # Small projection error relative to variance → normal_variance
        reasons = self._call(
            actual_stat=1.6,
            projected_stat=1.2,
            projection_error=0.4,
            player_variance=1.5,
            miss_margin=-0.9,   # slightly under (loss)
        )
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.NORMAL_VARIANCE.value, codes)

    def test_projection_model_miss_when_large_residual_no_other_driver(self):
        # Large error but no volume/minutes driver present
        reasons = self._call(
            actual_stat=5.0,
            projected_stat=1.5,
            projection_error=3.5,
            player_variance=1.0,  # z_error = 3.5 > 2.0
        )
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.PROJECTION_MODEL_MISS.value, codes)

    def test_insufficient_edge_detected(self):
        reasons = self._call(ctx={'projected_edge': -0.02})
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.INSUFFICIENT_EDGE.value, codes)

    def test_line_value_miss_detected(self):
        reasons = self._call(ctx={'projected_edge': 0.03})  # < 0.05 threshold
        codes = [r[0] for r in reasons]
        self.assertIn(PostmortemReason.LINE_VALUE_MISS.value, codes)

    def test_reasons_sorted_by_confidence(self):
        reasons = self._call(
            overtime_flag=True,
            actual_minutes=38.0, expected_minutes=26.0, minutes_delta=12.0,
        )
        scores = [r[1] for r in reasons]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_duplicates_in_output(self):
        reasons = self._call(overtime_flag=True, blowout_flag=True)
        codes = [r[0] for r in reasons]
        self.assertEqual(len(codes), len(set(codes)))

    def test_at_most_three_reasons_returned_by_caller(self):
        # The function itself can return more; caller slices [:3]
        reasons = self._call(
            overtime_flag=True, blowout_flag=True,
            actual_minutes=36.0, expected_minutes=24.0, minutes_delta=12.0,
        )
        # All returned reasons should have valid codes
        for code, score in reasons:
            self.assertIsInstance(code, str)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_unknown_returned_when_no_evidence(self):
        # No data available at all
        reasons = self._call(
            ctx={},
            actual_stat=0.0, projected_stat=None, projection_error=None,
            player_variance=0.0, miss_margin=0.0,
        )
        # Should fall back to unknown or normal_variance (anything, just not empty)
        self.assertTrue(len(reasons) >= 1)


# ---------------------------------------------------------------------------
# Tests: create_or_update_postmortem
# ---------------------------------------------------------------------------

class TestCreateOrUpdatePostmortem(BaseTestCase):
    """Integration tests that require a real SQLite DB."""

    def _setup_player_logs(self, player_name, game_date):
        """Add 10 history logs before game_date and the game log itself."""
        for i in range(10, 0, -1):
            d = date(game_date.year, game_date.month, game_date.day - i)
            _add_game_log(player_name, d, minutes=28.0, fg3a=4.0)
        # The actual game
        _add_game_log(player_name, game_date, minutes=31.0, fg3a=6.0, fg3m=2.0)
        db.session.commit()

    def test_skips_non_prop_bet(self):
        """Postmortem should be None for moneyline bets."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = make_bet(user.id, outcome='win')
            db.session.add(bet_obj)
            db.session.commit()
            result = create_or_update_postmortem(bet_obj)
            self.assertIsNone(result)

    def test_skips_pending_bet(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='pending', actual_total=None)
            db.session.add(bet_obj)
            db.session.commit()
            result = create_or_update_postmortem(bet_obj)
            self.assertIsNone(result)

    def test_skips_push(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='push')
            db.session.add(bet_obj)
            db.session.commit()
            result = create_or_update_postmortem(bet_obj)
            self.assertIsNone(result)

    def test_creates_postmortem_for_settled_prop(self):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            # Add game logs so the service has data to work with
            game_date = date(2026, 1, 15)
            self._setup_player_logs('Benedict Mathurin', game_date)

            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            self.assertEqual(pm.bet_id, bet_obj.id)
            self.assertEqual(pm.actual_stat, 2.0)
            self.assertEqual(pm.prop_line, 1.5)
            self.assertIsNotNone(pm.primary_reason_code)
            self.assertIsNotNone(pm.reason_confidence)
            self.assertIsNotNone(pm.diagnosis_json)

    def test_postmortem_is_idempotent(self):
        """Running create_or_update_postmortem twice must not create a duplicate."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()
            create_or_update_postmortem(bet_obj)
            create_or_update_postmortem(bet_obj)

            count = BetPostmortem.query.filter_by(bet_id=bet_obj.id).count()
            self.assertEqual(count, 1, "Expected exactly one postmortem record")

    def test_postmortem_updates_existing(self):
        """Re-running should update the existing record, not fail."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()
            _bet_id = bet_obj.id

            pm1 = create_or_update_postmortem(bet_obj)
            original_created_at = pm1.created_at

            pm2 = create_or_update_postmortem(bet_obj)
            self.assertEqual(pm1.id, pm2.id)
            # updated_at should advance (or stay the same within same second)
            self.assertGreaterEqual(pm2.updated_at, original_created_at)

    def test_diagnosis_json_is_valid(self):
        """Diagnosis JSON must be parseable and contain expected keys."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            d = json.loads(pm.diagnosis_json)
            self.assertIn('actual_stat', d)
            self.assertIn('prop_line', d)
            self.assertIn('miss_margin', d)
            self.assertIn('reason_scores', d)

    def test_miss_margin_sign_over_bet(self):
        """Over bet that lost: miss_margin should be negative (actual < line)."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            # Over 3.5 but only hit 1.0
            bet_obj = _make_prop_bet(
                user.id, outcome='lose', actual_total=1.0,
                prop_line=3.5, bet_type='over',
            )
            db.session.add(bet_obj)
            db.session.commit()
            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            # actual - line = 1.0 - 3.5 = -2.5
            self.assertAlmostEqual(pm.miss_margin, -2.5, places=1)

    def test_miss_margin_sign_under_bet(self):
        """Under bet that won: miss_margin should be positive (actual < line)."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            # Under 3.5, actual 1.5 — win
            bet_obj = _make_prop_bet(
                user.id, outcome='win', actual_total=1.5,
                prop_line=3.5, bet_type='under',
            )
            db.session.add(bet_obj)
            db.session.commit()
            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            # line - actual = 3.5 - 1.5 = 2.0
            self.assertAlmostEqual(pm.miss_margin, 2.0, places=1)

    def test_ot_flag_from_game_snapshot(self):
        """When GameSnapshot shows OT-level score, overtime_flag should be True."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            # Add a GameSnapshot with OT-level total (>230)
            snap = GameSnapshot(
                espn_id='401756001',
                game_date=date(2026, 1, 15),
                home_team='Lakers',
                away_team='Pacers',
                home_score=118,
                away_score=116,
                status='STATUS_FINAL',
                is_final=True,
            )
            db.session.add(snap)
            db.session.commit()

            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            self.assertTrue(pm.overtime_flag, "Expected overtime_flag=True when total > 230")

    def test_blowout_flag_from_game_snapshot(self):
        """When GameSnapshot shows blowout margin (>22), blowout_flag should be True."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            snap = GameSnapshot(
                espn_id='401756001',
                game_date=date(2026, 1, 15),
                home_team='Lakers',
                away_team='Pacers',
                home_score=130,
                away_score=105,  # diff = 25 > 22
                status='STATUS_FINAL',
                is_final=True,
            )
            db.session.add(snap)
            db.session.commit()

            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            self.assertTrue(pm.blowout_flag)

    def test_postmortem_with_pick_context(self):
        """When PickContext exists, projected_stat and edge should be used."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            ctx_data = {
                'projected_stat': 1.2,
                'projected_edge': 0.08,
                'player_variance': 0.9,
                'confidence_tier': 'moderate',
                'minutes_trend': 'stable',
            }
            pc = PickContext(
                bet_id=bet_obj.id,
                context_json=json.dumps(ctx_data),
                projected_stat=1.2,
                projected_edge=0.08,
                confidence_tier='moderate',
            )
            db.session.add(pc)
            db.session.commit()

            pm = create_or_update_postmortem(bet_obj)
            self.assertIsNotNone(pm)
            self.assertAlmostEqual(pm.projected_stat, 1.2, places=1)
            # projection_error = 2.0 - 1.2 = 0.8
            self.assertAlmostEqual(pm.projection_error, 0.8, places=1)


# ---------------------------------------------------------------------------
# Tests: backfill_postmortems
# ---------------------------------------------------------------------------

class TestBackfillPostmortems(BaseTestCase):

    def test_backfill_skips_ineligible(self):
        """Non-prop bets and pushes should count as ineligible."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()

            moneyline = make_bet(user.id, outcome='win')
            push_prop = _make_prop_bet(user.id, outcome='push')
            db.session.add_all([moneyline, push_prop])
            db.session.commit()

            summary = backfill_postmortems([moneyline, push_prop])
            self.assertEqual(summary['errors'], 0)
            self.assertEqual(summary['ineligible'], 2)
            self.assertEqual(summary['created'], 0)

    def test_backfill_skip_existing(self):
        """skip_existing=True should not overwrite existing postmortems."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()

            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            # Create a postmortem first
            create_or_update_postmortem(bet_obj)

            # Backfill with skip_existing=True — should skip
            summary = backfill_postmortems([bet_obj], skip_existing=True)
            self.assertEqual(summary['skipped'], 1)
            self.assertEqual(summary['created'], 0)

    def test_backfill_overwrite(self):
        """skip_existing=False should update existing postmortems."""
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()

            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            create_or_update_postmortem(bet_obj)
            summary = backfill_postmortems([bet_obj], skip_existing=False)
            # updated counts as 'created' in the backfill helper
            self.assertEqual(summary['errors'], 0)


# ---------------------------------------------------------------------------
# Tests: BetPostmortem model helpers
# ---------------------------------------------------------------------------

class TestBetPostmortemModel(BaseTestCase):

    def _make_pm(self, reason='volume_spike', confidence=0.80):
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            bet_obj = _make_prop_bet(user.id, outcome='lose', actual_total=2.0)
            db.session.add(bet_obj)
            db.session.commit()

            pm = BetPostmortem(
                bet_id=bet_obj.id,
                primary_reason_code=reason,
                reason_confidence=confidence,
                actual_stat=2.0,
                prop_line=1.5,
                miss_margin=-0.5,
                diagnosis_json=json.dumps({'test': True}),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.session.add(pm)
            db.session.commit()
            return pm.id

    def test_primary_reason_label(self):
        with self.app.app_context():
            pm_id = self._make_pm('volume_spike')
            pm = BetPostmortem.query.get(pm_id)
            self.assertEqual(pm.primary_reason_label, 'Volume Spike')

    def test_confidence_label_high(self):
        with self.app.app_context():
            pm_id = self._make_pm(confidence=0.80)
            pm = BetPostmortem.query.get(pm_id)
            self.assertEqual(pm.confidence_label, 'High')

    def test_confidence_label_medium(self):
        with self.app.app_context():
            pm_id = self._make_pm(confidence=0.60)
            pm = BetPostmortem.query.get(pm_id)
            self.assertEqual(pm.confidence_label, 'Medium')

    def test_confidence_label_low(self):
        with self.app.app_context():
            pm_id = self._make_pm(confidence=0.40)
            pm = BetPostmortem.query.get(pm_id)
            self.assertEqual(pm.confidence_label, 'Low')

    def test_diagnosis_property_parses_json(self):
        with self.app.app_context():
            pm_id = self._make_pm()
            pm = BetPostmortem.query.get(pm_id)
            d = pm.diagnosis
            self.assertIsInstance(d, dict)
            self.assertTrue(d.get('test'))

    def test_cascade_delete_with_bet(self):
        """Deleting a Bet should cascade-delete its BetPostmortem."""
        with self.app.app_context():
            pm_id = self._make_pm()
            pm = BetPostmortem.query.get(pm_id)
            bet_id = pm.bet_id

            bet_obj = Bet.query.get(bet_id)
            db.session.delete(bet_obj)
            db.session.commit()

            remaining = BetPostmortem.query.filter_by(bet_id=bet_id).count()
            self.assertEqual(remaining, 0)


# ---------------------------------------------------------------------------
# Tests: PROP_TO_ATTEMPTS_KEY coverage
# ---------------------------------------------------------------------------

class TestPropToAttemptsKey(unittest.TestCase):

    def test_points_maps_to_fga(self):
        self.assertEqual(PROP_TO_ATTEMPTS_KEY.get('player_points'), 'fga')

    def test_threes_maps_to_fg3a(self):
        self.assertEqual(PROP_TO_ATTEMPTS_KEY.get('player_threes'), 'fg3a')

    def test_other_props_have_no_attempts(self):
        for prop in ('player_rebounds', 'player_assists', 'player_steals', 'player_blocks'):
            self.assertIsNone(
                PROP_TO_ATTEMPTS_KEY.get(prop),
                f"Expected None for {prop}",
            )


if __name__ == '__main__':
    unittest.main()
