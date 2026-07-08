# tests/test_historical_game_log.py
"""Tests for the permanent, sport-aware historical game log table."""

from datetime import date

from sqlalchemy.exc import IntegrityError

from app import db
from tests.helpers import BaseTestCase


def make_hist_row(**overrides):
    from app.models import HistoricalGameLog
    defaults = dict(
        sport='nba',
        player_id='2544',
        player_name='LeBron James',
        team_abbr='LAL',
        opp_abbr='BOS',
        game_id='0022400123',
        game_date=date(2026, 1, 15),
        season='2025-26',
        home_away='HOME',
        win_loss='W',
        starter=True,
        stats={'pts': 31.0, 'reb': 8.0, 'ast': 9.0},
    )
    defaults.update(overrides)
    return HistoricalGameLog(**defaults)


class TestHistoricalGameLog(BaseTestCase):

    def test_round_trip_with_json_stats(self):
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(make_hist_row())
            db.session.commit()
            row = HistoricalGameLog.query.one()
            self.assertEqual(row.sport, 'nba')
            self.assertEqual(row.stats['pts'], 31.0)
            self.assertEqual(row.season, '2025-26')
            self.assertTrue(row.starter)

    def test_unique_sport_player_game(self):
        with self.app.app_context():
            db.session.add(make_hist_row())
            db.session.commit()
            db.session.add(make_hist_row(stats={'pts': 99.0}))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_same_game_id_different_sport_allowed(self):
        from app.models import HistoricalGameLog
        with self.app.app_context():
            db.session.add(make_hist_row())
            db.session.add(make_hist_row(sport='mlb', stats={'hits': 2.0}))
            db.session.commit()
            self.assertEqual(HistoricalGameLog.query.count(), 2)

    def test_repr(self):
        row = make_hist_row()
        self.assertIn('LeBron James', repr(row))
        self.assertIn('nba', repr(row))
