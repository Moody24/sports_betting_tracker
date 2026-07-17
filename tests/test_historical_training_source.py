"""Tests for the permanent-store training adapter."""

from datetime import date, timedelta

from sqlalchemy.orm import object_session
from unittest.mock import patch

from app import db
from app.models import HistoricalGameLog, HistoricalGameOdds, PlayerGameLog
from tests.helpers import BaseTestCase


def seed_historical_player(
    player_id: str = "espn-1",
    count: int = 2,
    start: date = date(2025, 1, 1),
    team_abbr: str = "LAL",
    opp_abbr: str = "BOS",
    home_away: str = "home",
    total: float = 224.5,
):
    rows = []
    for index in range(count):
        game_date = start + timedelta(days=index)
        game_id = f"game-{player_id}-{index}"
        row = HistoricalGameLog(
            sport="nba",
            player_id=player_id,
            player_name=f"Historical {player_id}",
            team_abbr=team_abbr,
            opp_abbr=opp_abbr,
            game_id=game_id,
            game_date=game_date,
            season="2024-25",
            home_away=home_away,
            win_loss="W",
            starter=True,
            stats={
                "pts": 20.0 + index,
                "reb": 7.0,
                "ast": 6.0,
                "stl": 1.0,
                "blk": 0.5,
                "tov": 2.0,
                "fgm": 8.0,
                "fga": 16.0,
                "ftm": 3.0,
                "fta": 4.0,
                "fg3m": 1.0,
                "fg3a": 5.0,
                "minutes": 34.0,
                "plus_minus": 4.0,
                "usage_pct": 27.0,
            },
        )
        db.session.add(row)
        db.session.add(HistoricalGameOdds(
            game_date=game_date,
            home_abbr=team_abbr if home_away == "home" else opp_abbr,
            away_abbr=opp_abbr if home_away == "home" else team_abbr,
            spread=3.5,
            favored="home",
            total=total,
            is_playoff=False,
            espn_game_id=game_id,
        ))
        rows.append(row)
    db.session.commit()
    return rows


class TestHistoricalTrainingSource(BaseTestCase):

    def test_adapter_flattens_stats_and_synthesizes_live_matchup(self):
        from app.services.historical_training_source import load_historical_training_logs

        with self.app.app_context():
            seed_historical_player(home_away="home")
            seed_historical_player(
                player_id="espn-2",
                start=date(2025, 2, 1),
                team_abbr="NYK",
                opp_abbr="MIA",
                home_away="away",
            )

            logs = load_historical_training_logs()

            home = next(log for log in logs if log.player_id == "espn-1")
            away = next(log for log in logs if log.player_id == "espn-2")
            self.assertIsInstance(home, PlayerGameLog)
            self.assertEqual(home.pts, 20.0)
            self.assertEqual(home.minutes, 34.0)
            self.assertEqual(home.plus_minus, 4.0)
            self.assertEqual(home.home_away, "home")
            self.assertEqual(home.matchup, "LAL vs. BOS")
            self.assertEqual(away.home_away, "away")
            self.assertEqual(away.matchup, "NYK @ MIA")
            self.assertFalse(hasattr(home, "starter"))
            self.assertFalse(hasattr(home, "usage_pct"))

    def test_adapter_returns_ordered_transient_objects_without_session_writes(self):
        from app.services.historical_training_source import load_historical_training_logs

        with self.app.app_context():
            seed_historical_player(count=3)
            self.assertFalse(db.session.new)
            self.assertFalse(db.session.dirty)

            logs = load_historical_training_logs()

            self.assertEqual(
                [(log.player_id, log.game_date) for log in logs],
                sorted((log.player_id, log.game_date) for log in logs),
            )
            self.assertTrue(all(log.id is None for log in logs))
            self.assertTrue(all(log.cache_expires is None for log in logs))
            self.assertTrue(all(object_session(log) is None for log in logs))
            self.assertFalse(db.session.new)
            self.assertFalse(db.session.dirty)

    def test_adapter_filters_by_sport(self):
        from app.services.historical_training_source import load_historical_training_logs

        with self.app.app_context():
            seed_historical_player()
            db.session.add(HistoricalGameLog(
                sport="mlb",
                player_id="mlb-1",
                player_name="Baseball Player",
                team_abbr="TOR",
                opp_abbr="NYY",
                game_id="mlb-game",
                game_date=date(2025, 4, 1),
                season="2025",
                home_away="home",
                stats={"hits": 2},
            ))
            db.session.commit()

            logs = load_historical_training_logs()

            self.assertTrue(logs)
            self.assertTrue(all(log.player_id.startswith("espn-") for log in logs))


class TestTrainingSourceSelection(BaseTestCase):

    def test_point_rows_use_historical_store_when_cache_is_empty(self):
        from app.services.ml_model import _build_training_rows

        with self.app.app_context():
            seed_historical_player(count=15)

            rows = _build_training_rows("player_points", min_train_samples=1)

            self.assertTrue(rows)
            self.assertEqual({row[1] for row in rows}, {"espn-1"})

    def test_nonempty_historical_store_below_threshold_does_not_use_large_cache(self):
        from app.services.ml_model import _build_training_rows
        from tests.test_distributional_model import _seed_dist_logs

        with self.app.app_context():
            seed_historical_player(count=5)
            _seed_dist_logs(player_id="cache-1", count=40)

            rows = _build_training_rows("player_points", min_train_samples=20)

            self.assertEqual(rows, [])

    def test_historical_point_rows_carry_espn_game_joined_total(self):
        from app.services.ml_model import _build_training_rows

        with self.app.app_context():
            seed_historical_player(count=15, home_away="home", total=231.5)

            rows = _build_training_rows("player_points", min_train_samples=1)

            self.assertTrue(rows)
            self.assertEqual({row[2]["game_total_line"] for row in rows}, {231.5})

    def test_historical_distributional_away_rows_carry_joined_total(self):
        from app.services import distributional_model as dm
        from unittest.mock import patch

        with self.app.app_context():
            seed_historical_player(
                count=15,
                team_abbr="MIA",
                opp_abbr="NYK",
                home_away="away",
                total=219.5,
            )

            with patch.object(dm, "MIN_TRAIN_SAMPLES", 1):
                rows = dm._build_dist_training_rows("player_points")

            self.assertTrue(rows)
            self.assertEqual({row[2]["game_total_line"] for row in rows}, {219.5})
            self.assertEqual(PlayerGameLog.query.count(), 0)

    def test_point_rows_fall_back_to_cache_when_historical_store_is_empty(self):
        from app.services.ml_model import _build_training_rows
        from tests.test_distributional_model import _seed_dist_logs

        with self.app.app_context():
            _seed_dist_logs(player_id="cache-1", count=15)

            rows = _build_training_rows("player_points", min_train_samples=1)

            self.assertTrue(rows)
            self.assertEqual({row[1] for row in rows}, {"cache-1"})

    def test_point_rows_never_union_historical_and_cache_sources(self):
        from app.services.ml_model import _build_training_rows
        from tests.test_distributional_model import _seed_dist_logs

        with self.app.app_context():
            seed_historical_player(count=15)
            _seed_dist_logs(player_id="cache-1", count=15)

            rows = _build_training_rows("player_points", min_train_samples=1)

            self.assertTrue(rows)
            self.assertEqual({row[1] for row in rows}, {"espn-1"})

    def test_distributional_rows_prefer_historical_store_over_cache(self):
        from app.services import distributional_model as dm
        from tests.test_distributional_model import _seed_dist_logs

        with self.app.app_context():
            seed_historical_player(count=15)
            _seed_dist_logs(player_id="cache-1", count=15)

            with patch.object(dm, "MIN_TRAIN_SAMPLES", 1):
                rows = dm._build_dist_training_rows("player_points")

            self.assertTrue(rows)
            self.assertEqual({row[1] for row in rows}, {"espn-1"})


class TestHistoricalBaselineReplay(BaseTestCase):

    def test_replay_uses_strictly_prior_historical_logs_in_live_order(self):
        from app.services.distributional_model import replay_running_baseline

        with self.app.app_context():
            historical = seed_historical_player(count=90)
            current = historical[-1]
            row = (
                current.game_date,
                current.player_id,
                {"game_total_line": 224.5},
                current.stats["pts"],
            )

            with patch(
                "app.services.projection_engine.ProjectionEngine.project_stat",
                autospec=True,
                return_value={"projection": 24.5, "std_dev": 4.25},
            ) as project:
                result = replay_running_baseline(row, "player_points")

            self.assertEqual(result, (24.5, 4.25))
            engine = project.call_args.args[0]
            cached_logs = engine._player_state_cache[current.player_name.lower()][1]
            self.assertEqual(len(cached_logs), 82)
            self.assertEqual(
                [log.game_date for log in cached_logs],
                sorted((log.game_date for log in cached_logs), reverse=True),
            )
            self.assertTrue(all(log.game_date < current.game_date for log in cached_logs))


class TestHistoricalDistributionalTraining(BaseTestCase):

    def test_distributional_training_succeeds_with_only_historical_store(self):
        from app.models import ModelMetadata
        from app.services import distributional_model as dm

        with self.app.app_context():
            seed_historical_player(
                player_id="espn-101",
                count=40,
                start=date(2024, 1, 1),
                team_abbr="LAL",
                opp_abbr="BOS",
            )
            seed_historical_player(
                player_id="espn-102",
                count=40,
                start=date(2024, 3, 1),
                team_abbr="NYK",
                opp_abbr="MIA",
            )
            seed_historical_player(
                player_id="espn-103",
                count=40,
                start=date(2024, 5, 1),
                team_abbr="DEN",
                opp_abbr="PHX",
            )
            self.assertEqual(PlayerGameLog.query.count(), 0)

            with patch.object(dm, "MIN_TRAIN_SAMPLES", 50):
                rows = dm._build_dist_training_rows("player_points")
                result = dm.train_distributional_model("player_points")

            self.assertEqual(len(rows), 90)
            self.assertNotIn("error", result)
            self.assertGreater(result["train_samples"], 0)
            self.assertIsNotNone(ModelMetadata.query.filter_by(
                model_name="dist_player_points",
                is_active=True,
            ).first())
