"""Tests for the game-day coordinator tick state machine."""

from datetime import datetime, date, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.helpers import BaseTestCase, make_bet, make_user

ET = ZoneInfo("America/New_York")


def _game(espn_id='401800123', status='STATUS_FINAL', tip_et_hour=19,
          season_type=2, game_date=(2026, 11, 6)):
    # start_time is UTC in ESPN payloads; compute via real tz conversion
    # (Nov 6 2026 is standard time, ET = UTC-5).
    tip_et = datetime(*game_date, tip_et_hour, tzinfo=ET)
    return {
        'espn_id': espn_id, 'status': status,
        'home': {'abbr': 'LAL', 'score': 120},
        'away': {'abbr': 'GS', 'score': 110},
        'start_time': tip_et.astimezone(timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ'),
        'season_type': season_type,
    }


NOW_EVENING = datetime(2026, 11, 6, 22, 0, tzinfo=ET)   # 10 PM ET game night
NOW_MORNING = datetime(2026, 11, 6, 9, 0, tzinfo=ET)


class CoordinatorBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        from app.services import game_day_coordinator as gdc
        gdc._DAY_CACHE.clear()


@patch('app.services.game_day_coordinator.resolve_and_grade')
@patch('app.services.game_day_coordinator.append_final_game')
@patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
class TestTiers(CoordinatorBase):

    def test_no_games_day_is_dormant_and_caches(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = []
        with self.app.app_context():
            self.assertEqual(run_tick(now=NOW_MORNING), 'dormant')
            first_calls = mock_sb.call_count      # today + 3 lookback dates
            self.assertEqual(run_tick(now=NOW_MORNING), 'dormant')
            self.assertEqual(mock_sb.call_count, first_calls)   # zero network
        mock_rag.assert_not_called()

    def test_pregame_before_first_tip_minus_lead(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game(status='STATUS_SCHEDULED', tip_et_hour=19)]
        with self.app.app_context():
            # 9 AM, tip 7 PM → pre-game, and no second scoreboard fetch
            self.assertEqual(run_tick(now=NOW_MORNING), 'pre-game')

    def test_live_window_fetches_fresh_and_detects_final(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick
        # today's scoreboard has the final game; lookback dates have nothing
        # (a single game cannot legitimately be "final" on 3 different past
        # calendar dates -- only today's fetch should surface it).
        mock_sb.side_effect = lambda date_str=None: (
            [_game(status='STATUS_FINAL')] if date_str is None else [])
        mock_app.return_value = 25
        with self.app.app_context():
            tier = run_tick(now=NOW_EVENING)
        self.assertEqual(tier, 'live')
        mock_app.assert_called_once()             # chain fired for the game
        mock_rag.assert_called_once()

    def test_stale_offseason_scoreboard_goes_dormant(self, mock_sb, mock_app, mock_rag):
        # ESPN's dateless scoreboard returns the LAST PLAYED league day
        # during the off-season (a month-old final), not an empty list.
        from app.services.game_day_coordinator import run_tick
        stale_final = _game(status='STATUS_FINAL', game_date=(2026, 6, 13))
        now = datetime(2026, 7, 10, 12, 0, tzinfo=ET)
        mock_sb.side_effect = lambda date_str=None: (
            [stale_final] if date_str is None else [])
        with self.app.app_context():
            self.assertEqual(run_tick(now=now), 'dormant')
            calls_after_first = mock_sb.call_count
            self.assertEqual(run_tick(now=now), 'dormant')
            self.assertEqual(mock_sb.call_count, calls_after_first)
        mock_app.assert_not_called()
        mock_rag.assert_not_called()

    def test_post_when_all_final_and_nothing_needed(self, mock_sb, mock_app, mock_rag):
        from app.services import game_day_coordinator as gdc
        mock_sb.side_effect = lambda date_str=None: (
            [_game(status='STATUS_FINAL')] if date_str is None else [])
        mock_app.return_value = 0
        with self.app.app_context():
            with patch.object(gdc, 'history_rows_exist', return_value=True):
                gdc.run_tick(now=NOW_EVENING)          # first: live pass
                tier = gdc.run_tick(now=NOW_EVENING)   # nothing left → post
                self.assertEqual(tier, 'post')
                sb_calls = gdc.fetch_espn_scoreboard.call_count
                gdc.run_tick(now=NOW_EVENING)          # done-cached → dormant
                self.assertEqual(
                    gdc.fetch_espn_scoreboard.call_count, sb_calls)


@patch('app.services.game_day_coordinator.resolve_and_grade')
@patch('app.services.game_day_coordinator.append_final_game')
@patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
class TestChainAndCatchUp(CoordinatorBase):

    def test_chain_skips_resolve_when_no_pending_and_no_snapshot_diff(
            self, mock_sb, mock_app, mock_rag):
        # final game whose history is missing but bets/snapshots agree
        # (snapshot already marked final, no pending bets):
        # append fires, resolve_and_grade does NOT
        from app import db
        from app.models import GameSnapshot
        from app.services.game_day_coordinator import run_tick
        mock_sb.side_effect = lambda date_str=None: (
            [_game()] if date_str is None else [])
        mock_app.return_value = 25
        with self.app.app_context():
            db.session.add(GameSnapshot(
                espn_id='401800123', game_date=date(2026, 11, 6),
                home_team='Lakers', away_team='Warriors', is_final=True))
            db.session.commit()
            run_tick(now=NOW_EVENING)
        mock_app.assert_called_once()
        mock_rag.assert_not_called()

    def test_pending_bets_trigger_resolve(self, mock_sb, mock_app, mock_rag):
        from app import db
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game()]
        mock_app.return_value = 0
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add(make_bet(
                user.id, match_date=datetime(2026, 11, 6, 19, 0),
                bet_type='total', outcome='pending'))
            db.session.commit()
            with patch('app.services.game_day_coordinator.history_rows_exist',
                       return_value=True):
                run_tick(now=NOW_EVENING)
        mock_rag.assert_called_once()

    def test_unfinalized_snapshot_triggers_resolve(self, mock_sb, mock_app, mock_rag):
        from app import db
        from app.models import GameSnapshot
        from app.services.game_day_coordinator import run_tick
        mock_sb.return_value = [_game()]
        mock_app.return_value = 0
        with self.app.app_context():
            db.session.add(GameSnapshot(
                espn_id='401800123', game_date=date(2026, 11, 6),
                home_team='Lakers', away_team='Warriors', is_final=False))
            db.session.commit()
            with patch('app.services.game_day_coordinator.history_rows_exist',
                       return_value=True):
                run_tick(now=NOW_EVENING)
        mock_rag.assert_called_once()

    def test_lookback_appends_missed_games(self, mock_sb, mock_app, mock_rag):
        # day-cache init scans LOOKBACK_DAYS past dates via date_str param
        from app.services.game_day_coordinator import run_tick, LOOKBACK_DAYS
        past_final = _game(espn_id='401800000')
        mock_sb.side_effect = lambda date_str=None: (
            [] if date_str is None else [past_final])
        mock_app.return_value = 20
        with self.app.app_context():
            run_tick(now=NOW_MORNING)
        dated = [c for c in mock_sb.call_args_list
                 if c.kwargs.get('date_str') or (c.args and c.args[0])]
        self.assertEqual(len(dated), LOOKBACK_DAYS)
        self.assertEqual(mock_app.call_count, LOOKBACK_DAYS)  # per past final

    def test_playoff_final_not_appended_but_still_graded(
            self, mock_sb, mock_app, mock_rag):
        # A playoff final must never be appended to HistoricalGameLog
        # (regular-season-only), but pending bets on it still need grading.
        from app import db
        from app.services.game_day_coordinator import run_tick
        mock_sb.side_effect = lambda date_str=None: (
            [_game(season_type=3)] if date_str is None else [])
        with self.app.app_context():
            user = make_user()
            db.session.add(user)
            db.session.commit()
            db.session.add(make_bet(
                user.id, match_date=datetime(2026, 11, 6, 19, 0),
                bet_type='total', outcome='pending'))
            db.session.commit()
            run_tick(now=NOW_EVENING)
        mock_app.assert_not_called()
        mock_rag.assert_called_once()

    def test_lookback_skips_non_regular_season(self, mock_sb, mock_app, mock_rag):
        from app.services.game_day_coordinator import run_tick, LOOKBACK_DAYS
        playoff_final = _game(espn_id='401800001', season_type=3)
        regular_final = _game(espn_id='401800002', season_type=2)
        mock_sb.side_effect = lambda date_str=None: (
            [] if date_str is None else [playoff_final, regular_final])
        mock_app.return_value = 20
        with self.app.app_context():
            run_tick(now=NOW_MORNING)
        self.assertEqual(mock_app.call_count, LOOKBACK_DAYS)
        for call in mock_app.call_args_list:
            self.assertEqual(call.args[0].get('espn_id'), '401800002')

    def test_joblog_written_for_chain(self, mock_sb, mock_app, mock_rag):
        from app.models import JobLog
        from app.services.game_day_coordinator import run_tick
        mock_sb.side_effect = lambda date_str=None: (
            [_game()] if date_str is None else [])
        mock_app.return_value = 25
        with self.app.app_context():
            run_tick(now=NOW_EVENING)
            job = JobLog.query.filter_by(job_name='game-final-chain').one()
            self.assertEqual(job.status, 'success')
            self.assertIn('401800123', job.message)


class TestWiring(CoordinatorBase):

    @patch('app.services.game_day_coordinator.run_tick', return_value='dormant')
    def test_coordinator_tick_cli(self, mock_tick):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=['coordinator-tick'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('dormant', result.output)
        mock_tick.assert_called_once()

    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard',
           return_value=[])
    def test_snapshot_props_odds_skips_on_empty_day(self, mock_sb):
        from app.services import scheduler as sched
        with patch('app.services.nba_service.snapshot_todays_props') as mock_snap:
            with self.app.app_context():
                sched.snapshot_props_odds()
            mock_snap.assert_not_called()

    @patch('app.services.game_day_coordinator.fetch_espn_scoreboard')
    def test_snapshot_props_odds_runs_on_game_day(self, mock_sb):
        from app.services import scheduler as sched
        mock_sb.return_value = [{'espn_id': '401800123',
                                 'status': 'STATUS_SCHEDULED'}]
        with patch('app.services.nba_service.snapshot_todays_props') as mock_snap:
            mock_snap.return_value = 5
            with self.app.app_context():
                sched.snapshot_props_odds()
            mock_snap.assert_called_once()
