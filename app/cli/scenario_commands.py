"""CLIs for the scenario engine (manual refresh + split inspection)."""

import click


@click.command('refresh-splits')
@click.option('--sport', default='nba', show_default=True)
@click.option('--force', is_flag=True, default=False,
              help='Refresh even when the store has no new rows.')
def cli_refresh_splits(sport, force):
    """Recompute and materialize all scenario splits."""
    from app.services.scenario_engine import refresh_splits
    result = refresh_splits(sport=sport, force=force)
    click.echo(f"Done: players={result['players']} rows={result['rows']}"
               + (f" skipped={result['skipped_reason']}"
                  if result['skipped_reason'] else ""))


@click.command('show-splits')
@click.option('--player', required=True)
@click.option('--stat', default='pts', show_default=True)
@click.option('--dim', default=None, help='Filter to one dimension.')
def cli_show_splits(player, stat, dim):
    """Print a player's materialized splits (single-dim rows)."""
    from app.models import ScenarioSplit
    q = ScenarioSplit.query.filter_by(player_name=player, stat=stat,
                                      season_scope='all', dim2=None)
    if dim:
        q = q.filter_by(dim1=dim)
    rows = q.order_by(ScenarioSplit.dim1, ScenarioSplit.bucket1).all()
    if not rows:
        click.echo("no splits found")
        return
    click.echo(f"{player} — {stat} (baseline {rows[0].baseline_mean:.1f})")
    for r in rows:
        click.echo(f"  {r.dim1}={r.bucket1:<12} n={r.n:<4} "
                   f"raw={r.raw_mean:.1f} shrunk={r.shrunk_mean:.1f}")


def register_scenario_commands(app):
    app.cli.add_command(cli_refresh_splits)
    app.cli.add_command(cli_show_splits)
