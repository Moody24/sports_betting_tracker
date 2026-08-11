"""CLI for seeding deterministic bet fixtures — local Playwright E2E use only.

Not gated behind an environment check: this is a CLI command, so it already
requires the same shell access as every other data-mutating command in this
package (``flask retrain --force``, ``flask backtest``, etc.). It must never
be wired to an HTTP route.
"""

from datetime import date, datetime, time, timedelta

import click

from app import db
from app.enums import BetType, Outcome
from app.models import Bet, User

FIXTURE_MARKER = 'AUTO_BOOTSTRAP_HIDDEN:E2E_FIXTURE'


def _fixture_bets(user_id: int, today: datetime) -> list[Bet]:
    return [
        Bet(
            user_id=user_id, team_a='Lakers', team_b='Celtics',
            match_date=today - timedelta(days=2), bet_amount=75.0,
            american_odds=-120, outcome=Outcome.WIN.value,
            bet_type=BetType.MONEYLINE.value, picked_team='Lakers',
            notes=FIXTURE_MARKER,
        ),
        Bet(
            user_id=user_id, team_a='Nuggets', team_b='Suns',
            match_date=today - timedelta(days=1), bet_amount=40.0,
            american_odds=-115, outcome=Outcome.LOSE.value,
            bet_type=BetType.OVER.value, player_name='LeBron James',
            prop_type='player_points', prop_line=27.5, actual_total=21.0,
            notes=FIXTURE_MARKER,
        ),
        Bet(
            user_id=user_id, team_a='Bucks', team_b='Heat',
            match_date=today - timedelta(days=1), bet_amount=30.0,
            american_odds=-110, outcome=Outcome.PUSH.value,
            bet_type=BetType.UNDER.value, over_under_line=224.5,
            actual_total=224.5, notes=FIXTURE_MARKER,
        ),
        Bet(
            user_id=user_id, team_a='Warriors', team_b='Kings',
            match_date=today + timedelta(days=1), bet_amount=25.0,
            american_odds=-105, outcome=Outcome.PENDING.value,
            bet_type=BetType.OVER.value, player_name='Stephen Curry',
            prop_type='player_threes', prop_line=4.5,
            notes=FIXTURE_MARKER,
        ),
        Bet(
            user_id=user_id, team_a='Celtics', team_b='Knicks',
            match_date=today, bet_amount=50.0, american_odds=-110,
            outcome=Outcome.PENDING.value, bet_type=BetType.OVER.value,
            player_name='Jayson Tatum', prop_type='player_points',
            prop_line=24.5, external_game_id='401585183',
            notes=FIXTURE_MARKER,
        ),
        Bet(
            user_id=user_id, team_a='Nuggets', team_b='Timberwolves',
            match_date=today, bet_amount=35.0, american_odds=-110,
            outcome=Outcome.PENDING.value, bet_type=BetType.UNDER.value,
            player_name='Nikola Jokic', prop_type='player_rebounds',
            prop_line=9.5, external_game_id='401585190',
            notes=FIXTURE_MARKER,
        ),
    ]


@click.command('e2e-seed-bets')
@click.option('--username', required=True, help='Existing user to seed bets for.')
def cli_e2e_seed_bets(username):
    """Seed a deterministic set of bets for Playwright visual-fixture tests.

    Idempotent: clears any previously seeded fixture bets for the user first.
    Local/E2E use only — never expose this behavior over HTTP.
    """
    user = User.query.filter_by(username=username).first()
    if user is None:
        raise click.ClickException(f'No user found with username {username!r}')

    Bet.query.filter_by(user_id=user.id, notes=FIXTURE_MARKER).delete()

    # Matches the naive `date.today()` the bets-list route uses for its
    # live_trackable freshness check (app/routes/bet_crud.py `now_date`).
    today = datetime.combine(date.today(), time(12, 0))
    for bet in _fixture_bets(user.id, today):
        db.session.add(bet)
    db.session.commit()
    click.echo(f'Seeded 6 fixture bets for {username!r}.')


def register_e2e_commands(app):
    app.cli.add_command(cli_e2e_seed_bets)
