"""Tests for the live-name -> ESPN-id crosswalk resolver."""

from datetime import datetime, timezone

from app import db
from app.models import ScenarioSplit
from tests.helpers import BaseTestCase


def _seed_split(player_id: str, player_name: str):
    db.session.add(ScenarioSplit(
        sport='nba', player_id=player_id, player_name=player_name,
        stat='pts', dim1='home_away', bucket1='home', season_scope='all',
        n=10, raw_mean=20.0, shrunk_mean=19.5, baseline_mean=19.0,
        computed_at=datetime.now(timezone.utc)))
    db.session.commit()


class TestNormalizeName(BaseTestCase):

    def test_strips_accents_case_punctuation_and_suffixes(self):
        from app.services.player_crosswalk import normalize_name
        self.assertEqual(normalize_name('Nikola Jokić'), 'nikola jokic')
        self.assertEqual(normalize_name('Jaren Jackson Jr.'),
                         'jaren jackson')
        self.assertEqual(normalize_name("De'Aaron Fox"), 'deaaron fox')
        self.assertEqual(normalize_name('Trey Murphy III'), 'trey murphy')


class TestResolveEspnId(BaseTestCase):

    def test_resolves_and_caches(self):
        from app.services import player_crosswalk as xw
        with self.app.app_context():
            _seed_split('4396971', 'Nikola Jokić')
            xw.clear_cache()
            self.assertEqual(xw.resolve_espn_id('Nikola Jokic'), '4396971')

    def test_collision_drops_both_never_guesses(self):
        from app.services import player_crosswalk as xw
        with self.app.app_context():
            _seed_split('111', 'Jalen Williams')
            _seed_split('222', 'Jaylen Williams')
            _seed_split('333', 'Jalen Williams')
            xw.clear_cache()
            self.assertIsNone(xw.resolve_espn_id('Jalen Williams'))
            self.assertEqual(xw.resolve_espn_id('Jaylen Williams'), '222')

    def test_unresolved_returns_none_and_override_wins(self):
        from app.services import player_crosswalk as xw
        with self.app.app_context():
            xw.clear_cache()
            self.assertIsNone(xw.resolve_espn_id('Nobody Man'))
            xw.OVERRIDES['nobody man'] = '999'
            try:
                self.assertEqual(xw.resolve_espn_id('Nobody Man'), '999')
            finally:
                xw.OVERRIDES.pop('nobody man')
