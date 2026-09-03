"""Repeatable SQLite-to-PostgreSQL copy and validation tooling."""

from __future__ import annotations

import json
from pathlib import Path

import click
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine, make_url

from app import db


def _source_url(value: str) -> str:
    """Accept an explicit SQLite URL or a filesystem path."""
    if '://' in value:
        return value
    return f"sqlite:///{Path(value).expanduser().resolve()}"


def _validate_url_pair(
    source: str,
    target: str,
    *,
    require_postgres: bool = True,
) -> tuple[str, str]:
    source_url = _source_url(source)
    source_parsed = make_url(source_url)
    target_parsed = make_url(target)
    if source_parsed.get_backend_name() != 'sqlite':
        raise click.ClickException('Source must be a SQLite path or URL.')
    if require_postgres and target_parsed.get_backend_name() != 'postgresql':
        raise click.ClickException('Target must be a PostgreSQL URL.')
    if source_parsed == target_parsed:
        raise click.ClickException('Source and target must be different databases.')
    return source_url, target


def _app_tables():
    return tuple(db.metadata.sorted_tables)


def _assert_schema_ready(source_engine: Engine, target_engine: Engine) -> None:
    expected = {table.name for table in _app_tables()}
    source_tables = set(inspect(source_engine).get_table_names())
    target_tables = set(inspect(target_engine).get_table_names())
    missing_source = sorted(expected - source_tables)
    missing_target = sorted(expected - target_tables)
    if missing_source:
        raise click.ClickException(
            'Source schema is not current; missing tables: '
            + ', '.join(missing_source)
        )
    if missing_target:
        raise click.ClickException(
            'Target migrations are not at head; missing tables: '
            + ', '.join(missing_target)
        )


def _row_counts(connection) -> dict[str, int]:
    return {
        table.name: int(
            connection.execute(
                select(func.count()).select_from(table)
            ).scalar_one()
        )
        for table in _app_tables()
    }


def _group_metric(connection, table, columns: tuple[str, ...]) -> dict[str, int]:
    selected = [table.c[name] for name in columns]
    rows = connection.execute(
        select(*selected, func.count()).group_by(*selected)
    ).all()
    return {
        '|'.join('null' if value is None else str(value) for value in row[:-1]): int(row[-1])
        for row in rows
    }


def _domain_metrics(connection) -> dict[str, object]:
    by_name = {table.name: table for table in _app_tables()}
    metrics: dict[str, object] = {}
    bet = by_name.get('bet')
    if bet is not None:
        metrics['bet_amount_total'] = float(
            connection.execute(
                select(func.coalesce(func.sum(bet.c.bet_amount), 0.0))
            ).scalar_one()
        )
        metrics['bets_by_user_outcome'] = _group_metric(
            connection, bet, ('user_id', 'outcome')
        )
    historical = by_name.get('historical_game_log')
    if historical is not None:
        group_columns = tuple(
            name for name in ('sport', 'season') if name in historical.c
        )
        if group_columns:
            metrics['historical_logs_by_sport_season'] = _group_metric(
                connection, historical, group_columns
            )
    odds = by_name.get('odds_snapshots')
    if odds is not None:
        group_columns = tuple(
            name for name in ('sportsbook', 'snapshot_kind') if name in odds.c
        )
        if group_columns:
            metrics['quotes_by_kind_book'] = _group_metric(
                connection, odds, group_columns
            )
    return metrics


def _reset_postgres_sequences(connection) -> None:
    if connection.dialect.name != 'postgresql':
        return
    for table in _app_tables():
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) != 1:
            continue
        column = primary_keys[0]
        if not column.autoincrement or not hasattr(column.type, 'python_type'):
            continue
        if column.type.python_type is not int:
            continue
        sequence_name = connection.execute(
            text(
                'SELECT pg_get_serial_sequence(:table_name, :column_name)'
            ),
            {'table_name': table.name, 'column_name': column.name},
        ).scalar_one_or_none()
        if not sequence_name:
            continue
        max_value = connection.execute(
            select(func.max(column))
        ).scalar_one_or_none() or 0
        connection.execute(
            text(
                'SELECT setval('
                'CAST(:sequence_name AS regclass), :next_value, false)'
            ),
            {
                'sequence_name': sequence_name,
                'next_value': int(max_value) + 1,
            },
        )


def _validate_connections(source_connection, target_connection) -> dict:
    source_counts = _row_counts(source_connection)
    target_counts = _row_counts(target_connection)
    source_metrics = _domain_metrics(source_connection)
    target_metrics = _domain_metrics(target_connection)
    table_results = {
        table: {
            'source': source_counts[table],
            'target': target_counts[table],
            'match': source_counts[table] == target_counts[table],
        }
        for table in source_counts
    }
    if target_connection.dialect.name == 'postgresql':
        target_connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    elif target_connection.dialect.name == 'sqlite':
        violations = target_connection.exec_driver_sql(
            'PRAGMA foreign_key_check'
        ).all()
        if violations:
            raise click.ClickException('Target foreign-key validation failed.')
    return {
        'version': 'DatabaseCopyReportV1',
        'valid': (
            all(result['match'] for result in table_results.values())
            and source_metrics == target_metrics
        ),
        'tables': table_results,
        'domain_metrics_match': source_metrics == target_metrics,
        'domain_metrics': target_metrics,
    }


def migrate_database(
    source_engine: Engine,
    target_engine: Engine,
    *,
    batch_size: int,
    dry_run: bool,
    validate_only: bool,
) -> dict:
    """Copy current app tables transactionally, or validate an existing copy."""
    _assert_schema_ready(source_engine, target_engine)
    with source_engine.connect() as source_connection:
        if validate_only:
            with target_engine.connect() as target_connection:
                return _validate_connections(
                    source_connection, target_connection
                )

        with target_engine.begin() as target_connection:
            target_counts = _row_counts(target_connection)
            nonempty = {
                table: count for table, count in target_counts.items() if count
            }
            if nonempty:
                raise click.ClickException(
                    'Target app tables must be empty before copy: '
                    + ', '.join(sorted(nonempty))
                )
            if dry_run:
                source_counts = _row_counts(source_connection)
                return {
                    'version': 'DatabaseCopyReportV1',
                    'valid': True,
                    'dry_run': True,
                    'tables': {
                        name: {'source': count, 'target': 0}
                        for name, count in source_counts.items()
                    },
                }

            for table in _app_tables():
                result = source_connection.execute(select(table))
                while rows := result.mappings().fetchmany(batch_size):
                    target_connection.execute(table.insert(), list(rows))
            _reset_postgres_sequences(target_connection)
            report = _validate_connections(
                source_connection, target_connection
            )
            if not report['valid']:
                raise click.ClickException(
                    'Validation failed; the target transaction was rolled back.'
                )
            return report


def register_database_commands(app) -> None:
    @app.cli.command('migrate-sqlite-to-postgres')
    @click.option('--source', required=True, help='SQLite file path or URL.')
    @click.option('--target', required=True, help='PostgreSQL URL.')
    @click.option('--dry-run', is_flag=True, help='Inspect without copying rows.')
    @click.option('--batch-size', type=click.IntRange(1, 10_000), default=500)
    @click.option('--validate-only', is_flag=True, help='Validate an existing copy.')
    @click.option('--report', type=click.Path(dir_okay=False, path_type=Path))
    def migrate_sqlite_to_postgres(
        source: str,
        target: str,
        dry_run: bool,
        batch_size: int,
        validate_only: bool,
        report: Path | None,
    ) -> None:
        """Copy a current SQLite database into a migrated PostgreSQL target."""
        source_url, target_url = _validate_url_pair(source, target)
        result = migrate_database(
            create_engine(source_url),
            create_engine(target_url),
            batch_size=batch_size,
            dry_run=dry_run,
            validate_only=validate_only,
        )
        output = json.dumps(result, indent=2, sort_keys=True)
        if report:
            report.write_text(output + '\n', encoding='utf-8')
            click.echo(f'Validation report written to {report.name}.')
        click.echo(output)
        if not result['valid']:
            raise click.ClickException('Database validation failed.')
