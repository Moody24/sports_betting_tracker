"""Focused tests for diagnostics, prop imports, and profitability metrics."""

import csv
import tempfile
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app import db
from app.models import ModelEvaluationRun, OddsSnapshot
from app.services.model_diagnostics import begin_evaluation, finish_evaluation, regression_summary
from app.services.rolling_backtest import promotion_verdict, summarize_bets
from tests.helpers import BaseTestCase


class TestEvaluationRuns(BaseTestCase):
    def test_persists_reproducible_evaluation(self):
        with self.app.app_context(), patch(
            'app.services.model_diagnostics._git_revision', return_value='abc123'
        ):
            run = begin_evaluation('model_diagnostics', 'player_points', {'folds': 3})
            finish_evaluation(run, {'train_mae': 3.0}, verdict='OK')
            stored = db.session.get(ModelEvaluationRun, run.id)
            self.assertEqual(stored.status, 'success')
            self.assertEqual(stored.code_revision, 'abc123')
            self.assertIn('train_mae', stored.metrics_json)

    def test_regression_summary_flags_large_generalization_gap(self):
        result = regression_summary(
            [1, 2, 3, 4], [1, 2, 3, 4],
            [1, 2, 3, 4], [10, 10, 10, 10],
        )
        self.assertTrue(result['likely_overfit'])


class TestPropOddsImporter(BaseTestCase):
    @staticmethod
    def _valid_row(**overrides):
        row = {
            'source_event_id': 'event-1',
            'event_start_time': '2026-11-01T19:30:00Z',
            'snapped_at': '2026-11-01T18:30:00Z',
            'player_name': 'Player A',
            'market': 'player_points',
            'bookmaker': 'fanduel',
            'line': '25.5',
            'over_odds': '-110',
            'under_odds': '-110',
        }
        row.update(overrides)
        return row

    def test_import_is_idempotent_and_normalizes_naive_et(self):
        from app.cli.prop_odds_import import import_player_prop_odds

        fields = [
            'source_event_id', 'event_start_time', 'snapped_at', 'player_name',
            'market', 'bookmaker', 'line', 'over_odds', 'under_odds',
        ]
        row = {
            'source_event_id': 'event-1',
            'event_start_time': '2026-11-01T19:30:00',
            'snapped_at': '2026-11-01T18:30:00',
            'player_name': 'LeBron James Jr.',
            'market': 'player_points',
            'bookmaker': 'fanduel',
            'line': '25.5',
            'over_odds': '-110',
            'under_odds': '-110',
        }
        with tempfile.NamedTemporaryFile('w', suffix='.csv', newline='', delete=False) as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
            path = handle.name
        with self.app.app_context(), patch(
            'app.cli.prop_odds_import.resolve_espn_id', return_value='123'
        ):
            first = import_player_prop_odds(path, 'csv', 'fixture')
            second = import_player_prop_odds(path, 'csv', 'fixture')
            self.assertEqual(first['inserted'], 1)
            self.assertEqual(second['skipped'], 1)
            stored = OddsSnapshot.query.one()
            self.assertEqual(stored.player_key, 'lebron james')
            self.assertEqual(stored.player_id, '123')

    def test_rejects_post_tip_snapshot(self):
        from app.cli.prop_odds_import import import_player_prop_odds

        payload = (
            '[{"source_event_id":"e","event_start_time":"2026-11-01T19:30:00Z",'
            '"snapped_at":"2026-11-01T19:31:00Z","player_name":"Player A",'
            '"market":"player_points","bookmaker":"fanduel","line":20.5,'
            '"over_odds":-110,"under_odds":-110}]'
        )
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            handle.write(payload)
            path = handle.name
        with self.app.app_context():
            result = import_player_prop_odds(path, 'json', 'fixture')
            self.assertEqual(result['rejected'], 1)
            self.assertEqual(OddsSnapshot.query.count(), 0)

    def test_snapshot_kind_windows_are_enforced(self):
        from app.cli.prop_odds_import import _validated_snapshot_times

        decision = self._valid_row(snapshot_kind='decision')
        _, _, kind = _validated_snapshot_times(decision)
        self.assertEqual(kind, 'decision')

        too_early = self._valid_row(
            snapshot_kind='decision',
            snapped_at='2026-11-01T18:00:00Z',
        )
        with self.assertRaisesRegex(ValueError, 'decision snapshot'):
            _validated_snapshot_times(too_early)

        valid_close = self._valid_row(
            snapshot_kind='close',
            snapped_at='2026-11-01T19:20:00Z',
        )
        _, _, kind = _validated_snapshot_times(valid_close)
        self.assertEqual(kind, 'close')

    def test_duplicate_key_skips_player_resolution(self):
        from app.cli.prop_odds_import import _build_imported_snapshot

        row = self._valid_row(source_snapshot_key='fixture:known')
        with patch('app.cli.prop_odds_import.resolve_espn_id') as resolver:
            snapshot, key, resolved = _build_imported_snapshot(
                row,
                'fixture',
                {'fixture:known'},
            )

        self.assertIsNone(snapshot)
        self.assertEqual(key, 'fixture:known')
        self.assertFalse(resolved)
        resolver.assert_not_called()


class TestProfitabilityMetrics(BaseTestCase):
    def _bet(self, outcome='win', close_line=26.5):
        return {
            'game_id': 'g1',
            'event_start_time': datetime(2026, 11, 1, tzinfo=timezone.utc),
            'side': 'over',
            'line': 25.5,
            'odds': -110,
            'entry_over_odds': -110,
            'entry_under_odds': -110,
            'close_line': close_line,
            'close_over_odds': -120,
            'close_under_odds': 100,
            'probability': 0.60,
            'outcome': outcome,
        }

    def test_roi_clv_and_push_accounting(self):
        from app.services.rolling_backtest import summarize_segments

        rows = [self._bet('win'), self._bet('push')]
        for row in rows:
            row.update({'fold': '2026-11-01', 'bookmaker': 'fanduel'})
        metrics = summarize_bets(rows)
        self.assertEqual(metrics['wins'], 1)
        self.assertEqual(metrics['pushes'], 1)
        self.assertEqual(metrics['hit_rate'], 1.0)
        self.assertGreater(metrics['flat_roi'], 0)
        self.assertGreater(metrics['mean_line_clv'], 0)
        segments = summarize_segments(rows)
        self.assertEqual(segments['bookmaker']['fanduel']['bets'], 2)

    def test_promotion_is_shadow_below_coverage_threshold(self):
        metrics = summarize_bets([self._bet('win')])
        verdict = promotion_verdict(metrics, metrics, {
            'settled_joined': 1, 'decision_opportunities': 1, 'folds_scored': 1,
        })
        self.assertEqual(verdict, 'SHADOW')


class TestRealQuoteSelection(BaseTestCase):
    def test_selects_like_for_like_consensus_and_close(self):
        from app.services.rolling_backtest import _quote_groups

        start = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)
        with self.app.app_context():
            for book, line, over_odds in (
                ('fanduel', 25.5, -105),
                ('draftkings', 26.5, 110),
            ):
                db.session.add(OddsSnapshot(
                    game_id='g1', source_event_id='e1', game_date=date(2026, 11, 1),
                    event_start_time=start, player_id='123', player_name='Player A',
                    player_key='player a', market='player_points', bookmaker=book,
                    line=line, over_odds=over_odds, under_odds=-110,
                    snapshot_kind='decision', snapped_at=start - timedelta(minutes=60),
                ))
                db.session.add(OddsSnapshot(
                    game_id='g1', source_event_id='e1', game_date=date(2026, 11, 1),
                    event_start_time=start, player_id='123', player_name='Player A',
                    player_key='player a', market='player_points', bookmaker=book,
                    line=line + 1, over_odds=-110, under_odds=-110,
                    snapshot_kind='close', snapped_at=start - timedelta(minutes=10),
                ))
            db.session.commit()

            quotes, coverage = _quote_groups(
                'player_points', date(2026, 11, 1), date(2026, 11, 1),
            )
            self.assertEqual(len(quotes), 2)
            self.assertTrue(all(row['bookmaker'] == 'fanduel' for row in quotes))
            self.assertTrue(all(row['line'] == 25.5 for row in quotes))
            self.assertTrue(all(row['close_line'] == 26.5 for row in quotes))
            self.assertEqual(coverage['decision_opportunities'], 1)

    def test_removes_current_only_defense_features(self):
        from app.services.rolling_backtest import _point_in_time_rows

        rows = [(date(2026, 1, 1), '1', {
            'opp_def_rating': 110.0, 'opp_pace': 99.0,
            'opp_stat_allowed': 25.0, 'games_played': 10.0,
        }, 20.0)]
        cleaned = _point_in_time_rows(rows)
        self.assertEqual(cleaned[0][2]['opp_def_rating'], 0.0)
        self.assertEqual(cleaned[0][2]['games_played'], 10.0)
