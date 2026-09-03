"""Flask CLI adapter for the hoopR historical-log import service."""

import click

from app.services.hoopr_import_service import (
    SEASON_TYPE_CODES,
    import_hoopr_seasons,
)


@click.command('import-hoopr-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--seasons', default=3, show_default=True, type=int)
@click.option('--season-type', default='Regular Season', show_default=True,
              type=click.Choice(sorted(SEASON_TYPE_CODES)))
@click.option('--from-dir', default=None,
              help='Read player_box_{year}.parquet files from a local '
                   'directory instead of downloading from GitHub.')
@click.option('--max-games', default=None, type=int,
              help='Cap games imported per season (whole games kept) — '
                   'for small-batch validation runs.')
@click.option('--update-stats', is_flag=True, default=False,
              help='Merge missing stats-payload keys into existing rows '
                   '(never overwrites keys already present).')
def cli_import_hoopr_logs(sport, seasons, season_type, from_dir, max_games,
                          update_stats):
    """Backfill HistoricalGameLog from hoopR (ESPN) data dumps on GitHub."""
    if sport != 'nba':
        raise click.BadParameter(
            f"sport '{sport}' not supported yet (nba only; mlb/nfl are "
            "Phase 3/4)")
    result = import_hoopr_seasons(
        sport=sport,
        seasons=seasons,
        season_type=season_type,
        from_dir=from_dir,
        max_games=max_games,
        update_stats=update_stats,
    )
    for warning in result['warnings']:
        click.echo(warning)
    click.echo(
        f"Done: inserted={result['inserted']} "
        f"skipped={result['skipped']} updated={result['updated']}"
        + (f" errors={'; '.join(result['errors'])}"
           if result['errors'] else "")
    )


def register_hoopr_import_commands(app):
    app.cli.add_command(cli_import_hoopr_logs)
