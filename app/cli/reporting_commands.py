"""Reporting Flask CLI commands."""

import logging

import click

logger = logging.getLogger(__name__)


@click.command('generate-auto-picks')
def cli_generate_auto_picks():
    from app.services.scheduler import generate_daily_auto_picks
    click.echo('Generating daily auto picks...')
    generate_daily_auto_picks()
    click.echo('Done.')


def register_reporting_commands(app):
    app.cli.add_command(cli_generate_auto_picks)
