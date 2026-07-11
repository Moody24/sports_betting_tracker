"""Model-shape tests for ScenarioSplit and HistoricalGameOdds."""

from datetime import date

from tests.helpers import BaseTestCase


class TestScenarioModels(BaseTestCase):

    def test_scenario_split_roundtrip_and_unique(self):
        from app import db
        from app.models import ScenarioSplit
        from sqlalchemy.exc import IntegrityError
        with self.app.app_context():
            # NOTE: dim2/bucket2 are non-null here (pairwise split) so the
            # uq_scenario_split_key UniqueConstraint is actually exercised.
            # Standard SQL (SQLite and Postgres alike) treats NULL != NULL
            # in composite UNIQUE constraints, so a row with dim2=None,
            # bucket2=None would NOT collide with an identical row at the
            # DB level even though it's a logical duplicate. Single-dim
            # splits (the common case) rely on the scenario engine's
            # wholesale rebuild-and-replace strategy for de-duplication,
            # not this constraint, for that reason.
            kw = dict(sport='nba', player_id='1966',
                      player_name='LeBron James', stat='pts',
                      dim1='home_away', bucket1='HOME',
                      dim2='opp_position', bucket2='PG',
                      season_scope='all', n=41,
                      raw_mean=27.1, shrunk_mean=26.8, baseline_mean=26.2)
            db.session.add(ScenarioSplit(**kw))
            db.session.commit()
            row = ScenarioSplit.query.one()
            self.assertEqual(row.bucket1, 'HOME')
            self.assertIsNotNone(row.computed_at)
            db.session.add(ScenarioSplit(**kw))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_game_odds_roundtrip_and_unique(self):
        from app import db
        from app.models import HistoricalGameOdds
        from sqlalchemy.exc import IntegrityError
        with self.app.app_context():
            kw = dict(game_date=date(2026, 11, 5), home_abbr='LAL',
                      away_abbr='GSW', spread=6.5, favored='home',
                      total=224.5, is_playoff=False)
            db.session.add(HistoricalGameOdds(**kw))
            db.session.commit()
            row = HistoricalGameOdds.query.one()
            self.assertEqual(row.source, 'kaggle')       # default
            self.assertIsNone(row.espn_game_id)
            db.session.add(HistoricalGameOdds(**kw))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
