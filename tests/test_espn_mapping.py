"""Tests for shared ESPN↔NBA mapping helpers."""

from datetime import date

from tests.helpers import BaseTestCase


class TestEspnMapping(BaseTestCase):

    def test_normalize_abbr_maps_espn_aliases(self):
        from app.services.espn_mapping import normalize_abbr
        self.assertEqual(normalize_abbr('GS'), 'GSW')
        self.assertEqual(normalize_abbr('NO'), 'NOP')
        self.assertEqual(normalize_abbr('NY'), 'NYK')
        self.assertEqual(normalize_abbr('SA'), 'SAS')
        self.assertEqual(normalize_abbr('UTAH'), 'UTA')
        self.assertEqual(normalize_abbr('WSH'), 'WAS')
        self.assertEqual(normalize_abbr('BOS'), 'BOS')   # passthrough

    def test_nba_teams_is_the_30(self):
        from app.services.espn_mapping import NBA_TEAMS
        self.assertEqual(len(NBA_TEAMS), 30)
        self.assertIn('GSW', NBA_TEAMS)
        self.assertNotIn('GS', NBA_TEAMS)
        self.assertNotIn('STARS', NBA_TEAMS)

    def test_usage_pct_formula(self):
        from app.services.espn_mapping import usage_pct
        # LeBron fixture from test_hoopr_import: LAL totals min66 fga34 fta12 tov5
        expected = ((19 + 0.44 * 7 + 3) * (66 / 5)) / (36 * (34 + 0.44 * 12 + 5))
        self.assertAlmostEqual(
            usage_pct(19, 7, 3, 36, 66, 34, 12, 5), expected, places=9)

    def test_usage_pct_zero_minutes_is_zero(self):
        from app.services.espn_mapping import usage_pct
        self.assertEqual(usage_pct(1, 0, 0, 0, 66, 34, 12, 5), 0.0)

    def test_season_for_date(self):
        from app.services.espn_mapping import season_for_date
        self.assertEqual(season_for_date(date(2026, 11, 5)), '2026-27')
        self.assertEqual(season_for_date(date(2027, 3, 5)), '2026-27')
        self.assertEqual(season_for_date(date(2026, 7, 10)), '2025-26')

    def test_hoopr_import_still_exposes_behavior(self):
        # the CLI module must keep working after the extraction
        from app.cli.hoopr_import import _rows_from_player_box  # noqa: F401
