"""Manual CLI entry point for the game-day coordinator (off-season testing)."""

import click


@click.command('coordinator-tick')
def cli_coordinator_tick():
    """Run one coordinator tick and print the tier it acted in."""
    from app.services.game_day_coordinator import run_tick
    click.echo(f"tier: {run_tick()}")


def register_coordinator_commands(app):
    app.cli.add_command(cli_coordinator_tick)
