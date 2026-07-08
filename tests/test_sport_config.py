"""Tests for the per-sport stat catalog registry."""

from tests.helpers import BaseTestCase


class TestSportConfig(BaseTestCase):

    def test_nba_config_has_core_stats(self):
        from app.services.sport_config import get_stat_config
        cfg = get_stat_config('nba')
        self.assertEqual(cfg.sport_key, 'nba')
        for key in ('pts', 'reb', 'ast', 'stl', 'blk', 'fg3m', 'minutes'):
            self.assertIn(key, cfg.stat_keys)

    def test_mlb_and_nfl_configs_exist(self):
        from app.services.sport_config import SPORT_STAT_CONFIG
        self.assertIn('hits', SPORT_STAT_CONFIG['mlb'].stat_keys)
        self.assertIn('strikeouts_pitcher', SPORT_STAT_CONFIG['mlb'].stat_keys)
        self.assertIn('rec_yds', SPORT_STAT_CONFIG['nfl'].stat_keys)
        self.assertIn('pass_yds', SPORT_STAT_CONFIG['nfl'].stat_keys)

    def test_unknown_sport_raises_key_error(self):
        from app.services.sport_config import get_stat_config
        with self.assertRaises(KeyError):
            get_stat_config('cricket')

    def test_configs_are_immutable(self):
        from app.services.sport_config import get_stat_config
        cfg = get_stat_config('nba')
        with self.assertRaises(Exception):
            cfg.sport_key = 'other'
