"""Regressions for verified history and provider-failure decisions."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app import db
from app.models import HistoricalGameLog, InjuryReport, PlayerIdentity
from app.services.auto_pick_service import auto_pick_data_issues
from app.services.data_receipts import latest_fetch, record_fetch
from app.services.player_identity_service import (
    get_verified_history,
    link_nba_identity,
    sync_player_identities,
    verified_nba_id,
)
from app.services.release_data_service import (
    import_release_injuries,
    team_defense_from_release,
)
from app.utils.time_helpers import et_today
from tests.helpers import BaseTestCase


class TestDataIntegrity(BaseTestCase):
    def _history_row(self, player_id, name, game_id, game_date):
        db.session.add(HistoricalGameLog(
            sport='nba', player_id=player_id, player_name=name,
            team_abbr='BOS', opp_abbr='NYK', game_id=game_id,
            game_date=game_date, season='2025-26', home_away='HOME',
            stats={'pts': 24, 'reb': 8, 'ast': 5, 'minutes': 33},
        ))
        db.session.commit()

    def test_unique_espn_history_is_available_without_nba_cache(self):
        with self.app.app_context():
            self._history_row('es123', 'Example Player', '401', date(2026, 3, 1))
            self._history_row('es123', 'Example Player', '402', date(2026, 3, 3))
            logs = get_verified_history('Example Player', as_of=date(2026, 3, 3))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].pts, 24)
            self.assertEqual(logs[0].player_id, 'es123')

    def test_player_detail_uses_permanent_history_without_nba_id(self):
        with self.app.app_context():
            self._history_row('es123', 'Example Player', '401', et_today() - timedelta(days=1))
        self.register_and_login()
        response = self.client.get('/nba/player-analysis/Example%20Player')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['history_source'], 'espn_history')
        self.assertEqual(body['game_log'][0]['pts'], 24)
        self.assertEqual(body['history_observed_through'],
                         (et_today() - timedelta(days=1)).isoformat())

    def test_duplicate_name_stays_unresolved(self):
        with self.app.app_context():
            self._history_row('es123', 'Same Name', '401', date(2026, 3, 1))
            self._history_row('es456', 'Same Name', '402', date(2026, 3, 2))
            result = sync_player_identities()
            self.assertEqual(result['ambiguous'], 2)
            self.assertEqual(get_verified_history('Same Name', as_of=date(2026, 3, 3)), [])
            self.assertEqual(PlayerIdentity.query.count(), 2)
            self.assertIsNone(verified_nba_id('Same Name'))

    def test_reviewed_nba_link_is_required_for_automatic_identity(self):
        with self.app.app_context():
            self._history_row('es123', 'Example Player', '401', date(2026, 3, 1))
            sync_player_identities()
            self.assertIsNone(verified_nba_id('Example Player'))
            link_nba_identity('es123', 'nba456', 'Reviewed roster and game box')
            self.assertEqual(verified_nba_id('Example Player'), 'nba456')

    @patch('app.services.feature_engine.build_pick_context_features', return_value={'ok': True})
    def test_auto_pick_context_uses_espn_history_without_nba_link(self, features):
        from app.services.auto_pick_service import _build_auto_pick_context

        with self.app.app_context():
            self._history_row('es123', 'Example Player', '401',
                              et_today() - timedelta(days=1))
            bet = SimpleNamespace(
                player_name='Example Player', bet_type='over',
                prop_type='player_points', prop_line=23.5,
            )
            self.assertEqual(_build_auto_pick_context(bet, {}), {'ok': True})
            self.assertEqual(features.call_args.kwargs['player_id'], 'es123')

    @patch('app.services.feature_engine.get_cached_logs')
    def test_model2_features_use_permanent_history_before_cache(self, cached_logs):
        from app.services.feature_engine import build_pick_context_features

        with self.app.app_context():
            self._history_row('es123', 'Example Player', '401',
                              et_today() - timedelta(days=1))
            context = build_pick_context_features(
                player_name='Example Player', player_id='unused',
                prop_type='player_points', prop_line=23.5,
                american_odds=-110, projected_stat=24,
                projected_edge=0.1, confidence_tier='moderate',
            )
            self.assertEqual(context['player_games_this_season'], 1)
            cached_logs.assert_not_called()

    @patch('app.services.context_service._fetch_injury_report', return_value=([], False))
    def test_failed_injury_fetch_does_not_relabel_old_rows(self, _fetch):
        from app.services.context_service import refresh_injuries, player_availability

        with self.app.app_context():
            yesterday = et_today() - timedelta(days=1)
            db.session.add(InjuryReport(
                player_name='Example Player', status='out',
                date_reported=yesterday, source='espn',
            ))
            db.session.commit()
            self.assertEqual(refresh_injuries(), 0)
            self.assertEqual(InjuryReport.query.count(), 1)
            self.assertEqual(InjuryReport.query.first().date_reported, yesterday)
            self.assertEqual(player_availability('Example Player'), 'unknown')

    def test_successful_zero_injury_report_verifies_unlisted_player(self):
        from app.services.context_service import player_availability

        with self.app.app_context():
            record_fetch('espn_injuries', 'success', 0, data_through=et_today())
            db.session.commit()
            self.assertEqual(player_availability('Example Player'), 'available')

    @patch('app.services.context_service._fetch_injury_report', return_value=([], True))
    def test_empty_direct_report_conflicting_with_release_abstains(self, _fetch):
        from app.services.context_service import player_availability, refresh_injuries

        with self.app.app_context():
            db.session.add(InjuryReport(
                player_name='Example Player', status='out',
                date_reported=et_today(), source='sportsdataverse_espn',
            ))
            db.session.commit()
            self.assertEqual(refresh_injuries(), 0)
            self.assertEqual(InjuryReport.query.count(), 1)
            self.assertEqual(latest_fetch('espn_injuries').status, 'conflict')
            self.assertEqual(player_availability('Example Player'), 'unknown')

    def test_probable_designation_is_not_confirmed_availability(self):
        from app.services.context_service import player_availability

        with self.app.app_context():
            db.session.add(InjuryReport(
                player_name='Example Player', status='probable',
                date_reported=et_today(), source='espn',
            ))
            record_fetch('espn_injuries', 'success', 1,
                         data_through=et_today(), source_version='espn_site')
            db.session.commit()
            self.assertEqual(player_availability('Example Player'), 'unknown')

    def test_auto_pick_abstains_on_missing_data(self):
        with self.app.app_context():
            score = {
                'player': 'Example Player', 'history_source': 'espn_history',
                'history_observed_through': et_today().isoformat(),
                'quote_observed_at': datetime.now(timezone.utc).isoformat(),
                'quote_source': 'odds_api',
                'odds_event_id': 'event1', 'player_team': 'Boston Celtics',
                'opponent_team': 'New York Knicks',
            }
            issues = auto_pick_data_issues(score, et_today())
            self.assertIn('injury availability unverified', issues)
            self.assertIn('opponent defense unverified', issues)

    @patch('app.services.matchup_service.get_team_defense')
    def test_prior_season_defense_cannot_verify_auto_pick(self, defense):
        defense.return_value = {
            'source': 'sportsdataverse_team_prior',
            'observed_through': date(2026, 4, 12), 'sample_games': 82,
        }
        with self.app.app_context():
            record_fetch('espn_injuries', 'success', 0, data_through=et_today())
            db.session.commit()
            score = {
                'player': 'Example Player', 'history_source': 'espn_history',
                'history_observed_through': et_today().isoformat(),
                'quote_observed_at': datetime.now(timezone.utc).isoformat(),
                'quote_source': 'odds_api', 'odds_event_id': 'event1',
                'player_team': 'Boston Celtics',
                'opponent_team': 'New York Knicks',
            }
            self.assertIn('opponent defense unverified',
                          auto_pick_data_issues(score, et_today()))

    def test_prop_quote_uses_bookmaker_update_time(self):
        from app.services.nba_service import _consensus_prop

        result = _consensus_prop('Example Player', {
            'fanduel': {'line': 24.5, 'over_odds': -110,
                        'under_odds': -110,
                        'last_update': '2026-10-01T14:00:00Z'},
            'draftkings': {'line': 24.5, 'over_odds': -105,
                           'under_odds': -115,
                           'last_update': '2026-10-01T14:03:00Z'},
        })
        self.assertEqual(result['source_last_update'],
                         '2026-10-01T14:00:00+00:00')

    def test_snapshot_quote_cannot_become_auto_pick(self):
        with self.app.app_context():
            score = {
                'player': 'Example Player', 'history_source': 'espn_history',
                'history_observed_through': et_today().isoformat(),
                'quote_observed_at': datetime.now(timezone.utc).isoformat(),
                'quote_source': 'snapshot_fallback', 'odds_event_id': 'event1',
                'player_team': 'Boston Celtics',
                'opponent_team': 'New York Knicks',
            }
            self.assertIn('offered prop quote unverified',
                          auto_pick_data_issues(score, et_today()))

    def test_team_release_uses_opponent_boxscore(self):
        rows = [
            {'game_id': 401, 'team_id': 1, 'opponent_team_id': 2,
             'team_display_name': 'Team One', 'team_abbreviation': 'BOS',
             'season_type': 2, 'game_date': date(2026, 3, 1),
             'field_goals_attempted': 80, 'offensive_rebounds': 8,
             'total_turnovers': 12, 'free_throws_attempted': 20,
             'team_score': 110, 'total_rebounds': 44, 'assists': 25,
             'three_point_field_goals_made': 12, 'steals': 8, 'blocks': 5},
            {'game_id': 401, 'team_id': 2, 'opponent_team_id': 1,
             'team_display_name': 'Team Two', 'team_abbreviation': 'NY',
             'season_type': 2, 'game_date': date(2026, 3, 1),
             'field_goals_attempted': 82, 'offensive_rebounds': 9,
             'total_turnovers': 11, 'free_throws_attempted': 18,
             'team_score': 102, 'total_rebounds': 40, 'assists': 22,
             'three_point_field_goals_made': 11, 'steals': 6, 'blocks': 4},
        ]
        result = team_defense_from_release(pd.DataFrame(rows), date(2026, 3, 2))
        by_team = {item['team_abbr']: item for item in result}
        self.assertEqual(by_team['BOS']['opp_pts_pg'], 102)
        self.assertEqual(by_team['NYK']['opp_pts_pg'], 110)
        self.assertEqual(by_team['BOS']['sample_games'], 1)

    @patch('app.services.release_data_service.load_release')
    def test_injury_release_preserves_as_of_date(self, load):
        yesterday = et_today() - timedelta(days=1)
        load.return_value = (pd.DataFrame([{
            'as_of_date': yesterday,
            'athlete_display_name': 'Example Player',
            'team_display_name': 'Boston Celtics',
            'status': 'Out', 'short_comment': 'Knee',
        }]), 'test-version')
        with self.app.app_context():
            result = import_release_injuries(2027)
            self.assertEqual(result['date'], yesterday)
            self.assertEqual(InjuryReport.query.one().date_reported, yesterday)

    @patch('app.services.release_data_service.load_release')
    def test_release_conflict_does_not_clear_direct_injury_status(self, load):
        load.return_value = (pd.DataFrame([{
            'as_of_date': et_today(),
            'athlete_display_name': 'Example Player',
            'team_display_name': 'Boston Celtics',
            'status': 'Out', 'short_comment': 'Knee',
        }]), 'release-test')
        with self.app.app_context():
            record_fetch('espn_injuries', 'success', 0,
                         data_through=et_today(), source_version='espn_site')
            db.session.commit()
            result = import_release_injuries(2027)
            self.assertEqual(result['status'], 'conflict')
            self.assertEqual(InjuryReport.query.count(), 0)
            self.assertEqual(latest_fetch('espn_injuries').status, 'conflict')
            self.assertEqual(import_release_injuries(2027)['status'], 'conflict')
            self.assertEqual(InjuryReport.query.count(), 0)
