"""Shared fixtures for the split service test modules."""

from datetime import date, timedelta

from app import db
from app.models import InjuryReport, PlayerGameLog, TeamDefenseSnapshot


def _seed_player_logs(count=20, player_id='101', player_name='LeBron James',
                      base_pts=25.0, base_reb=7.0, base_ast=7.0, base_fg3m=2.0,
                      base_minutes=35.0):
    """Insert ``count`` game logs for one player.  Returns the list of logs."""
    logs = []
    for i in range(count):
        log = PlayerGameLog(
            player_id=player_id,
            player_name=player_name,
            team_abbr='LAL',
            game_date=date(2026, 1, 1) + timedelta(days=i),
            matchup='LAL vs. BOS' if i % 2 == 0 else 'LAL @ MIA',
            minutes=base_minutes + (i % 5) - 2,
            pts=base_pts + (i % 7) - 3,
            reb=base_reb + (i % 4) - 1,
            ast=base_ast + (i % 3) - 1,
            fg3m=base_fg3m + (i % 3) - 1,
            stl=1.0 + (i % 2),
            blk=0.5 + (i % 2) * 0.5,
            tov=2.0,
            fgm=9.0,
            fga=18.0,
            ftm=5.0,
            fta=6.0,
            fg3a=5.0,
            plus_minus=3.0,
            home_away='home' if i % 2 == 0 else 'away',
            win_loss='W' if i % 3 != 0 else 'L',
        )
        db.session.add(log)
        logs.append(log)
    db.session.commit()
    return logs


def _seed_defense(team_name='Boston Celtics', team_abbr='BOS',
                  opp_pts=108.0, pace=98.5, def_rating=106.5):
    snap = TeamDefenseSnapshot(
        team_id='2',
        team_name=team_name,
        team_abbr=team_abbr,
        snapshot_date=date(2026, 2, 25),
        opp_pts_pg=opp_pts,
        opp_reb_pg=42.0,
        opp_ast_pg=24.0,
        opp_3pm_pg=11.0,
        opp_stl_pg=7.0,
        opp_blk_pg=4.5,
        opp_tov_pg=13.5,
        pace=pace,
        def_rating=def_rating,
    )
    db.session.add(snap)
    db.session.commit()
    return snap


class _FakeDataFrame:
    """Minimal DataFrame-like object for nba_api endpoint mocks in tests."""

    def __init__(self, rows):
        self._rows = list(rows)

    @property
    def empty(self):
        return len(self._rows) == 0

    def head(self, n):
        return _FakeDataFrame(self._rows[:n])

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, row


def _seed_injury(player_name='LeBron James', status='questionable'):
    report = InjuryReport(
        player_name=player_name,
        team='Los Angeles Lakers',
        status=status,
        detail='Knee soreness',
        date_reported=date.today(),
    )
    db.session.add(report)
    db.session.commit()
    return report
