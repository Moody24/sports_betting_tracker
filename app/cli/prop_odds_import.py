"""Provider-neutral import of historical player-prop quote snapshots."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone

import click

from app import db
from app.config_display import SUPPORTED_PROP_MARKETS
from app.models import OddsSnapshot
from app.services.player_crosswalk import normalize_name, resolve_espn_id
from app.utils.time_helpers import ET


REQUIRED_FIELDS = {
    'source_event_id', 'event_start_time', 'snapped_at', 'player_name',
    'market', 'bookmaker', 'line', 'over_odds', 'under_odds',
}


def _parse_datetime(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(timezone.utc)


def _parse_odds(value) -> int | None:
    if value in (None, ''):
        return None
    odds = int(float(value))
    if odds == 0 or abs(odds) > 10000:
        raise ValueError(f'invalid American odds: {value}')
    return odds


def _load_rows(file_path: str, file_format: str) -> list[dict]:
    with open(file_path, encoding='utf-8') as handle:
        if file_format == 'csv':
            return list(csv.DictReader(handle))
        payload = json.load(handle)
    if isinstance(payload, dict):
        payload = payload.get('rows')
    if not isinstance(payload, list):
        raise ValueError('JSON input must be a list or an object containing rows')
    return payload


def _snapshot_key(source: str, row: dict, snapped_at: datetime) -> str:
    identity = '|'.join(str(row.get(key, '')).strip() for key in (
        'source_event_id', 'player_name', 'market', 'bookmaker', 'line',
        'over_odds', 'under_odds',
    ))
    payload = f'{source}|{snapped_at.isoformat()}|{identity}'
    return f'{source}:{hashlib.sha256(payload.encode()).hexdigest()}'


def import_player_prop_odds(file_path: str, file_format: str, source: str) -> dict:
    source = str(source or '').strip().lower()
    if not source or len(source) > 30:
        raise ValueError('source must contain 1-30 characters')
    rows = _load_rows(file_path, file_format)
    inserted = skipped = rejected = resolved = unresolved = 0
    errors = []
    existing_keys = {
        key for (key,) in db.session.query(OddsSnapshot.source_snapshot_key)
        .filter(OddsSnapshot.source_snapshot_key.isnot(None)).all()
    }
    pending = []
    for number, row in enumerate(rows, start=2 if file_format == 'csv' else 1):
        try:
            missing = REQUIRED_FIELDS - set(row)
            if missing:
                raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
            market = str(row['market']).strip()
            if market not in SUPPORTED_PROP_MARKETS:
                raise ValueError(f'unsupported market: {market}')
            event_start = _parse_datetime(row['event_start_time'])
            snapped_at = _parse_datetime(row['snapped_at'])
            if snapped_at >= event_start:
                raise ValueError('snapshot is at or after event start')
            line = float(row['line'])
            if line < 0 or line > 200:
                raise ValueError(f'invalid prop line: {line}')
            over_odds = _parse_odds(row.get('over_odds'))
            under_odds = _parse_odds(row.get('under_odds'))
            if over_odds is None and under_odds is None:
                raise ValueError('at least one side must have odds')
            kind = str(row.get('snapshot_kind') or 'imported').strip().lower()
            if kind not in {'scheduled', 'decision', 'close', 'imported'}:
                raise ValueError(f'invalid snapshot kind: {kind}')
            minutes_to_tip = (event_start - snapped_at).total_seconds() / 60.0
            if kind == 'decision' and not 55 <= minutes_to_tip <= 65:
                raise ValueError('decision snapshot must be between T-65 and T-55')
            if kind == 'close' and not 5 <= minutes_to_tip <= 15:
                raise ValueError('close snapshot must be between T-15 and T-5')
            key = str(row.get('source_snapshot_key') or '').strip()
            if not key:
                key = _snapshot_key(source, row, snapped_at)
            if len(key) > 160:
                raise ValueError('source_snapshot_key exceeds 160 characters')
            if key in existing_keys:
                skipped += 1
                continue
            player_name = str(row['player_name']).strip()
            if not player_name:
                raise ValueError('player_name is empty')
            bookmaker = str(row['bookmaker']).strip().lower()
            if not bookmaker or len(bookmaker) > 30:
                raise ValueError('bookmaker must contain 1-30 characters')
            player_id = str(row.get('player_id') or '').strip() or resolve_espn_id(player_name)
            if player_id:
                resolved += 1
            else:
                unresolved += 1
            pending.append(OddsSnapshot(
                game_id=str(row.get('game_id') or '').strip() or None,
                source_event_id=str(row['source_event_id']).strip(),
                game_date=event_start.astimezone(ET).date(),
                event_start_time=event_start,
                player_id=player_id,
                player_name=player_name,
                player_key=normalize_name(player_name),
                market=market,
                bookmaker=bookmaker,
                line=line,
                over_odds=over_odds,
                under_odds=under_odds,
                source=source,
                snapshot_kind=kind,
                source_snapshot_key=key,
                snapped_at=snapped_at,
            ))
            existing_keys.add(key)
            inserted += 1
        except (TypeError, ValueError) as exc:
            rejected += 1
            if len(errors) < 25:
                errors.append(f'row {number}: {exc}')

    if pending:
        db.session.add_all(pending)
        db.session.commit()
    return {
        'inserted': inserted,
        'skipped': skipped,
        'rejected': rejected,
        'resolved': resolved,
        'unresolved': unresolved,
        'errors': errors,
    }


@click.command('import-player-prop-odds')
@click.option('--file', 'file_path', required=True, type=click.Path(exists=True, dir_okay=False))
@click.option('--format', 'file_format', required=True, type=click.Choice(['csv', 'json']))
@click.option('--source', required=True, help='Licensed provider or dataset identifier.')
def cli_import_player_prop_odds(file_path, file_format, source):
    """Import canonical historical player-prop quotes."""
    result = import_player_prop_odds(file_path, file_format, source.strip().lower())
    click.echo(
        f"inserted={result['inserted']} skipped={result['skipped']} "
        f"rejected={result['rejected']} resolved={result['resolved']} "
        f"unresolved={result['unresolved']}"
    )
    for error in result['errors']:
        click.echo(f'  {error}')


def register_prop_odds_import_commands(app):
    app.cli.add_command(cli_import_player_prop_odds)
