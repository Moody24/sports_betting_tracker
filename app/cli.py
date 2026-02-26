"""Flask CLI commands for manual job triggers.

Usage (local or via Railway console):
    flask --app run.py refresh-stats
    flask --app run.py refresh-defense
    flask --app run.py refresh-injuries
    flask --app run.py run-projections
    flask --app run.py grade-bets
    flask --app run.py retrain
"""

import click
from flask import current_app


def register_cli(app):
    """Register all CLI commands with the Flask app."""

    @app.cli.command('refresh-stats')
    def cli_refresh_stats():
        """Manually trigger player stats refresh."""
        from app.services.scheduler import refresh_player_stats
        click.echo('Refreshing player stats...')
        refresh_player_stats()
        click.echo('Done.')

    @app.cli.command('refresh-defense')
    def cli_refresh_defense():
        """Manually trigger team defense data refresh."""
        from app.services.scheduler import refresh_defense_data
        click.echo('Refreshing defense data...')
        refresh_defense_data()
        click.echo('Done.')

    @app.cli.command('refresh-injuries')
    def cli_refresh_injuries():
        """Manually trigger injury report refresh."""
        from app.services.scheduler import refresh_injury_reports
        click.echo('Refreshing injury reports...')
        refresh_injury_reports()
        click.echo('Done.')

    @app.cli.command('run-projections')
    def cli_run_projections():
        """Manually trigger projection engine."""
        from app.services.scheduler import run_projections
        click.echo('Running projections...')
        run_projections()
        click.echo('Done.')

    @app.cli.command('grade-bets')
    def cli_grade_bets():
        """Manually trigger bet grading."""
        from app.services.scheduler import resolve_and_grade
        click.echo('Grading bets...')
        resolve_and_grade()
        click.echo('Done.')

    @app.cli.command('retrain')
    def cli_retrain():
        """Manually trigger model retrain (projection + pick quality)."""
        from app.services.scheduler import retrain_models
        click.echo('Retraining models...')
        retrain_models()
        click.echo('Done.')
