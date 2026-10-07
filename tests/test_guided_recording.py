"""Verified quote recording and durable retry safety use fixtures only."""

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import db
from app.enums import BetSource
from app.models import Bet, BetRecordingRequest, GuidedQuote, GameSnapshot, PickContext
from app.services.guided_quote import attach_quote_tokens
from app.services.bet_placement_service import BetPlacementError
from app.utils.time_helpers import ET
from tests.helpers import BaseTestCase


class GuidedRecordingTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user_id = self.register_and_login()

    def prop(self, *, line=20.5, odds=-110, observed=None, game_id='espn-fixture'):
        now = datetime.now(timezone.utc)
        observed = observed or now.isoformat()
        return {
            'player': 'Fixture Player', 'market': 'player_points',
            'quote_source': 'odds_api', 'quote_observed_at': observed,
            'line': line, 'over_odds': odds, 'under_odds': -115,
            'best_over_book': 'fanduel', 'best_under_book': 'fanduel',
            'books': {'fanduel': {'line': line, 'over_odds': odds, 'under_odds': -115,
                                  'last_update': observed}},
            'game_id': game_id, 'odds_event_id': 'odds-fixture',
            'selection_key': f'espn:{game_id}' if game_id else 'odds_api:odds-fixture',
            'game_status': 'scheduled', 'start_time': now.isoformat(),
            'match_date': now.astimezone(ET).date().isoformat(),
            'team_a': 'Fixture Away', 'team_b': 'Fixture Home',
        }

    def issue(self, **kwargs):
        prop = self.prop(**kwargs)
        with self.app.app_context():
            attach_quote_tokens([prop], self.user_id)
        return prop

    def payload(self, prop=None, side='over'):
        prop = prop or self.issue()
        return {'request_id': str(uuid.uuid4()), 'stake': 10, 'is_parlay': False, 'legs': [{
            'player_name': prop['player'], 'prop_type': prop['market'], 'bet_type': side,
            'prop_line': prop['line'], 'american_odds': prop[f'{side}_odds'],
            'game_id': prop['game_id'], 'selection_key': prop['selection_key'],
            'team_a': prop['team_a'], 'team_b': prop['team_b'], 'match_date': prop['match_date'],
            'quote_token': prop['quote_tokens'][side], 'quote_book': prop['quote_books'][side],
            'quote_source': prop['quote_source'], 'quote_observed_at': prop['quote_observed_at'],
            'quote_line': prop['line'], 'quote_american_odds': prop[f'{side}_odds'],
            'recorded_odds_edited': False,
        }]}

    def save(self, payload):
        return self.client.post('/nba/place-bets', json=payload)

    def assert_empty(self):
        with self.app.app_context():
            self.assertEqual(Bet.query.count(), 0)
            self.assertEqual(BetRecordingRequest.query.count(), 0)

    def test_guided_save_preserves_quote_and_never_fetches_provider(self):
        payload = self.payload()
        self._session_http_mock.reset_mock()
        with patch('requests.get') as get:
            response = self.save(payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        get.assert_not_called()
        self._session_http_mock.assert_not_called()
        with self.app.app_context():
            bet = Bet.query.one()
            self.assertEqual(bet.american_odds, -110)
            self.assertEqual(bet.prop_line, 20.5)
            self.assertEqual(bet.source, BetSource.NBA_PROPS.value)
            self.assertEqual(bet.external_game_id, 'espn-fixture')
            self.assertIsNotNone(BetRecordingRequest.query.one().response_json)

    def test_known_player_new_record_never_invokes_model_context_or_providers(self):
        prop = self.prop()
        prop['player'] = 'LeBron James'
        with self.app.app_context():
            attach_quote_tokens([prop], self.user_id)
        with patch('app.services.bet_placement_service.create_pick_context_for_bet') as context, \
             patch('requests.get') as provider:
            response = self.save(self.payload(prop))
        self.assertEqual(response.status_code, 200)
        context.assert_not_called()
        provider.assert_not_called()
        self._session_http_mock.assert_not_called()
        with self.app.app_context():
            self.assertEqual(PickContext.query.count(), 0)
            GuidedQuote.query.delete()
            db.session.commit()
            facts = json.loads(BetRecordingRequest.query.one().request_json)['legs'][0]
            self.assertEqual(facts['quote_source'], 'odds_api')
            self.assertEqual(facts['quote_book'], 'fanduel')
            self.assertEqual(facts['quote_line'], 20.5)
            self.assertEqual(facts['quote_american_odds'], -110)
            self.assertEqual(facts['quote_observed_at'], prop['quote_observed_at'])

    def test_legacy_missing_odds_never_fabricates_model_context(self):
        payload = {'stake': 10, 'legs': [{
            'team_a': 'Away', 'team_b': 'Home', 'match_date': '2020-01-01',
            'player_name': 'LeBron James', 'prop_type': 'player_points',
            'prop_line': 20.5, 'bet_type': 'over',
        }]}
        with patch('app.services.bet_placement_service.create_pick_context_for_bet') as context:
            self.assertEqual(self.save(payload).status_code, 200)
        context.assert_not_called()
        with self.app.app_context():
            self.assertIsNone(Bet.query.one().american_odds)

    def test_odds_only_game_saves_no_counterfeit_espn_id(self):
        response = self.save(self.payload(self.issue(game_id='')))
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(Bet.query.one().external_game_id)

    def test_signature_tampering_and_user_replay_are_rejected(self):
        payload = self.payload()
        original = payload['legs'][0]['quote_token']
        payload['legs'][0]['quote_token'] = 'tampered.' + original
        self.assertEqual(self.save(payload).get_json()['code'], 'quote_invalid')
        payload['legs'][0]['quote_token'] = original
        self.client.post('/auth/logout')
        self.register_and_login('other-user', 'other@example.com')
        self.assertEqual(self.save(payload).get_json()['code'], 'quote_invalid')
        self.assert_empty()

    def test_guided_field_changes_require_explicit_manual_confirmation(self):
        original = self.payload()
        changes = {
            'prop_line': 21.5, 'american_odds': 120, 'quote_book': 'other',
            'player_name': 'Other Player', 'prop_type': 'player_rebounds',
            'bet_type': 'under', 'game_id': 'odds-fixture', 'selection_key': 'odds_api:wrong',
            'team_a': 'Other Away', 'match_date': '2000-01-01', 'quote_source': 'archive',
            'quote_observed_at': datetime.now(timezone.utc).isoformat(),
            'quote_line': 30.5, 'quote_american_odds': -120, 'recorded_odds_edited': True,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                payload = copy.deepcopy(original)
                payload['legs'][0][field] = value
                response = self.save(payload)
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.get_json()['code'], 'quote_changed')
        self.assert_empty()

    def test_expired_token_does_not_reserve_retry_key(self):
        payload = self.payload()
        with patch('app.services.guided_quote.is_fresh_prop_observation', return_value=False):
            response = self.save(payload)
        self.assertEqual(response.get_json()['code'], 'quote_expired')
        self.assert_empty()
        self.assertEqual(self.save(payload).status_code, 200)

    def test_newer_changed_quote_rejects_original_and_older_response_cannot_replace_it(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=30)
        original_prop = self.issue(observed=old.isoformat())
        payload = self.payload(original_prop)
        newer = self.issue(line=21.5, odds=-120)
        self.issue(observed=old.isoformat())
        response = self.save(payload)
        self.assertEqual(response.get_json()['code'], 'quote_changed')
        with self.app.app_context():
            values = [json.loads(row.payload_json)['line'] for row in GuidedQuote.query.all()]
            self.assertEqual(values, [21.5, 21.5])
        self.assertEqual(self.save(self.payload(newer)).status_code, 200)

    def test_newer_identical_quote_keeps_original_valid_within_original_ttl(self):
        prop = self.issue(observed=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
        payload = self.payload(prop)
        newer = copy.deepcopy(prop)
        newer['quote_observed_at'] = datetime.now(timezone.utc).isoformat()
        newer['books']['fanduel']['last_update'] = newer['quote_observed_at']
        with self.app.app_context():
            attach_quote_tokens([newer], self.user_id)
        self.assertEqual(self.save(payload).status_code, 200)

    def test_unavailable_quote_and_final_game_rejected(self):
        payload = self.payload()
        with self.app.app_context():
            attach_quote_tokens([], self.user_id, unavailable_events={'odds-fixture'})
        self.assertEqual(self.save(payload).get_json()['code'], 'quote_unavailable')
        payload = self.payload()
        with self.app.app_context():
            db.session.add(GameSnapshot(espn_id='espn-fixture', game_date=datetime.now(ET).date(),
                                        home_team='Fixture Home', away_team='Fixture Away', is_final=True))
            db.session.commit()
        self.assertEqual(self.save(payload).get_json()['code'], 'quote_unavailable')
        self.assert_empty()

    def test_withdrawal_watermark_blocks_delayed_quotes_and_only_newer_source_can_reopen(self):
        t0 = datetime.now(timezone.utc) - timedelta(minutes=1)
        t1, t2, t3 = [t0 + timedelta(seconds=offset) for offset in (10, 20, 30)]
        original = self.issue(observed=t0.isoformat())
        original_payload = self.payload(original)
        with self.app.app_context(), patch('app.services.guided_quote.datetime', wraps=datetime) as clock:
            clock.now.return_value = t2
            attach_quote_tokens([], self.user_id, unavailable_events={'odds-fixture'})
            for row in GuidedQuote.query.all():
                self.assertFalse(row.available)
                self.assertEqual(row.observed_at.replace(tzinfo=timezone.utc), t2)
                self.assertEqual(json.loads(row.payload_json)['observed_at'], t0.isoformat())
        for observed in (t0, t1, t2):
            with self.subTest(observed=observed):
                delayed = copy.deepcopy(original)
                delayed['quote_observed_at'] = observed.isoformat()
                delayed['books']['fanduel']['last_update'] = observed.isoformat()
                with self.app.app_context():
                    attach_quote_tokens([delayed], self.user_id, unavailable_events={'odds-fixture'})
                    self.assertFalse(any(row.available for row in GuidedQuote.query.all()))
                self.assertEqual(delayed['quote_tokens'], {})
                self.assertEqual(self.save(original_payload).get_json()['code'], 'quote_unavailable')
        newer = copy.deepcopy(original)
        newer['quote_observed_at'] = t3.isoformat()
        newer['books']['fanduel']['last_update'] = t3.isoformat()
        with self.app.app_context():
            attach_quote_tokens([newer], self.user_id, unavailable_events={'odds-fixture'})
            self.assertTrue(all(row.available for row in GuidedQuote.query.all()))
        self.assertEqual(set(newer['quote_tokens']), {'over', 'under'})
        self.assertEqual(self.save(self.payload(newer)).status_code, 200)

    def test_manual_historical_price_is_kept_without_live_evidence(self):
        payload = self.payload()
        leg = payload['legs'][0]
        leg.pop('quote_token')
        leg.update(manual_entry=True, american_odds=135, prop_line=18.5,
                   recorded_odds_edited=True, match_date='2020-01-01')
        response = self.save(payload)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            bet = Bet.query.one()
            self.assertEqual((bet.american_odds, bet.prop_line, bet.source), (135, 18.5, BetSource.MANUAL.value))
            self.assertEqual(bet.match_date.date().isoformat(), '2020-01-01')

    def test_new_requests_need_evidence_but_legacy_unsigned_records_are_manual(self):
        payload = {'request_id': str(uuid.uuid4()), 'stake': 10, 'legs': [{
            'team_a': 'Away', 'team_b': 'Home', 'match_date': '2020-01-01',
            'player_name': 'Fixture Player', 'prop_type': 'player_points',
            'prop_line': 20.5, 'bet_type': 'over', 'american_odds': -110,
        }]}
        self.assertEqual(self.save(payload).get_json()['code'], 'quote_required')
        payload.pop('request_id')
        self.assertEqual(self.save(payload).status_code, 200)
        with self.app.app_context():
            self.assertEqual(Bet.query.one().source, BetSource.MANUAL.value)

    def test_same_retry_returns_original_response_even_after_quote_expiry(self):
        payload = self.payload()
        first = self.save(payload)
        with self.app.app_context():
            db.session.remove()
        with patch('app.services.guided_quote.is_fresh_prop_observation', return_value=False):
            replay = self.save(payload)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json(), first.get_json())
        with self.app.app_context():
            self.assertEqual(Bet.query.count(), 1)
            self.assertEqual(BetRecordingRequest.query.count(), 1)

    def test_retry_key_with_different_payload_is_conflict(self):
        payload = self.payload()
        self.assertEqual(self.save(payload).status_code, 200)
        payload['stake'] = 25
        response = self.save(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'request_conflict')
        with self.app.app_context():
            self.assertEqual(Bet.query.count(), 1)

    def test_failed_persistence_rolls_back_bets_and_retry_receipt(self):
        payload = self.payload()
        def fail_after_flush(bets, **_kwargs):
            db.session.add_all(bets)
            db.session.flush()
            raise BetPlacementError('fixture failure')
        with patch('app.routes.nba_live.persist_new_bets', side_effect=fail_after_flush):
            self.assertEqual(self.save(payload).status_code, 400)
        self.assert_empty()
        self.assertEqual(self.save(payload).status_code, 200)

    def test_multiple_legs_validate_before_any_bet_is_persisted(self):
        payload = self.payload()
        payload['legs'].append({**payload['legs'][0], 'american_odds': 150})
        self.assertEqual(self.save(payload).status_code, 409)
        self.assert_empty()

    def test_quote_cleanup_removes_expired_observations(self):
        self.issue()
        with self.app.app_context():
            GuidedQuote.query.update({'observed_at': datetime.now(timezone.utc) - timedelta(minutes=11)})
            db.session.commit()
            attach_quote_tokens([], self.user_id)
            self.assertEqual(GuidedQuote.query.count(), 0)

    def test_all_props_only_signs_real_current_active_game_quotes(self):
        prop = self.prop()
        game = {'espn_id': prop['game_id'], 'odds_event_id': prop['odds_event_id'],
                'status': 'STATUS_SCHEDULED', 'start_time': prop['start_time'],
                'away': {'name': prop['team_a']}, 'home': {'name': prop['team_b']}}
        offered = {**prop, 'source_last_update': prop['quote_observed_at']}
        with patch('app.routes.nba_analysis.get_todays_games', return_value=[game]), \
             patch('app.routes.nba_analysis.fetch_upcoming_games', return_value=[]), \
             patch('app.routes.nba_analysis.fetch_player_props_for_event', return_value={'player_points': [offered]}) as fetch:
            response = self.client.get('/nba/all-props')
            self.assertEqual(set(response.get_json()[0]['quote_tokens']), {'over', 'under'})
            for status in ('STATUS_FINAL', 'STATUS_POSTPONED', 'unknown'):
                game['status'] = status
                response = self.client.get('/nba/all-props')
                self.assertEqual(response.get_json(), [])
            self.assertEqual(fetch.call_count, 1)

    def test_current_and_tomorrow_props_are_both_available_and_duplicate_events_fetch_once(self):
        prop = self.prop()
        now = datetime.now(ET)
        game = {'espn_id': 'today', 'odds_event_id': 'event-today', 'status': 'STATUS_SCHEDULED',
                'start_time': now.isoformat(), 'away': {'name': 'Away'}, 'home': {'name': 'Home'}}
        tomorrow = {**game, 'espn_id': '', 'odds_event_id': 'event-tomorrow',
                    'start_time': (now + timedelta(days=1)).isoformat()}
        offered = {**prop, 'source_last_update': prop['quote_observed_at']}
        with patch('app.routes.nba_analysis.get_todays_games', return_value=[game]), \
             patch('app.routes.nba_analysis.fetch_upcoming_games', return_value=[game, tomorrow]), \
             patch('app.routes.nba_analysis.fetch_player_props_for_event', return_value={'player_points': [offered]}) as fetch:
            rows = self.client.get('/nba/all-props').get_json()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual({row['selection_key'] for row in rows}, {'espn:today', 'odds_api:event-tomorrow'})
        self.assertTrue(all(set(row['quote_tokens']) == {'over', 'under'} for row in rows))

    def test_unverified_bookmaker_evidence_cannot_mint_token(self):
        for key, value in [('books', {}), ('game_status', 'final'), ('odds_event_id', '')]:
            with self.subTest(key=key), self.app.app_context():
                prop = self.prop()
                prop[key] = value
                attach_quote_tokens([prop], self.user_id)
                self.assertEqual(prop['quote_tokens'], {})

    def test_guided_token_cannot_bypass_durable_receipt_by_omitting_request_id(self):
        payload = self.payload()
        payload.pop('request_id')
        with patch('app.routes.nba_live.persist_new_bets') as persist:
            response = self.save(payload)
        self.assertEqual(response.status_code, 400)
        persist.assert_not_called()
        self.assert_empty()

    def test_retry_keys_are_user_scoped(self):
        payload = {'request_id': str(uuid.uuid4()), 'stake': 10, 'legs': [{
            'manual_entry': True, 'team_a': 'Away', 'team_b': 'Home',
            'bet_type': 'moneyline', 'picked_team': 'Away', 'american_odds': 125,
        }]}
        self.assertEqual(self.save(payload).status_code, 200)
        self.client.post('/auth/logout')
        self.register_and_login('another-user', 'another@example.com')
        self.assertEqual(self.save(payload).status_code, 200)
        with self.app.app_context():
            self.assertEqual(Bet.query.count(), 2)
            self.assertEqual(BetRecordingRequest.query.count(), 2)

    def test_malformed_uuid_and_nonfinite_extra_fields_rejected_before_persistence(self):
        payload = self.payload()
        payload['request_id'] = 'not-a-uuid'
        self.assertEqual(self.save(payload).status_code, 400)
        payload['request_id'] = str(uuid.uuid4())
        payload['unexpected'] = float('nan')
        self.assertEqual(self.save(payload).status_code, 400)
        self.assert_empty()


class GuidedRecordingMigrationTests(BaseTestCase):
    def test_migration_upgrade_and_downgrade_match_required_schema(self):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect

        migration = importlib.import_module('migrations.versions.f8b21c09a74d_add_guided_quote_and_recording_receipts')
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE user (id INTEGER PRIMARY KEY)')
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(migration, 'op', operations):
                migration.upgrade()
                schema = inspect(connection)
                self.assertEqual(schema.get_pk_constraint('bet_recording_request')['constrained_columns'],
                                 ['user_id', 'request_id'])
                columns = {column['name']: column for column in schema.get_columns('bet_recording_request')}
                self.assertFalse(columns['request_json']['nullable'])
                self.assertIn('guided_quote', schema.get_table_names())
                migration.downgrade()
                self.assertNotIn('guided_quote', inspect(connection).get_table_names())
        engine.dispose()
