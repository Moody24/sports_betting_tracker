"""Resolve ESPN player history without crossing identifier namespaces."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func

from app import db
from app.models import HistoricalGameLog, PlayerIdentity
from app.services.stats_service import _NBA_PLAYER_ID_OVERRIDES, _player_name_key


_STATS = ('pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'fgm', 'fga',
          'ftm', 'fta', 'fg3m', 'fg3a', 'minutes', 'plus_minus')


def sync_player_identities() -> dict[str, int]:
    """Index observed ESPN IDs; leave duplicate names unresolved."""
    rows = (db.session.query(
        HistoricalGameLog.player_id,
        HistoricalGameLog.player_name,
        HistoricalGameLog.team_abbr,
        func.max(HistoricalGameLog.game_date),
    ).filter_by(sport='nba')
        .filter(~HistoricalGameLog.game_id.like('00%'))
        .filter(HistoricalGameLog.player_name != '')
        .group_by(HistoricalGameLog.player_id, HistoricalGameLog.player_name,
                  HistoricalGameLog.team_abbr).all())
    recent = {}
    for espn_id, name, team, played_at in rows:
        if espn_id not in recent or played_at > recent[espn_id][2]:
            recent[espn_id] = (name, team, played_at)
    ids_by_name = {}
    for espn_id, (name, _, _) in recent.items():
        ids_by_name.setdefault(_player_name_key(name), set()).add(espn_id)

    existing = {row.espn_id: row for row in PlayerIdentity.query.all()}
    created = ambiguous = 0
    for espn_id, (name, team, _) in recent.items():
        identity = existing.get(espn_id)
        if identity is None:
            identity = PlayerIdentity(espn_id=espn_id)
            db.session.add(identity)
            existing[espn_id] = identity
            created += 1
        key = _player_name_key(name)
        identity.player_name = name
        identity.name_key = key
        identity.team_abbr = team
        if identity.status != 'linked':
            identity.status = 'ambiguous' if len(ids_by_name[key]) > 1 else 'source_verified'
            identity.evidence = 'historical_game_log:unique_espn_id'
        identity.checked_at = datetime.now(timezone.utc)
        ambiguous += identity.status == 'ambiguous'
    used_nba_ids = {row.nba_id: row.espn_id for row in existing.values()
                    if row.nba_id}
    for key, (_, nba_id) in _NBA_PLAYER_ID_OVERRIDES.items():
        matches = ids_by_name.get(key, set())
        if len(matches) == 1:
            identity = existing[next(iter(matches))]
            if (identity.nba_id not in (None, nba_id)
                    or used_nba_ids.get(nba_id) not in (None, identity.espn_id)):
                continue
            identity.nba_id = nba_id
            identity.status = 'linked'
            identity.evidence = 'nba.com verified override; unique ESPN name'
            used_nba_ids[nba_id] = identity.espn_id
    db.session.commit()
    return {'indexed': len(recent), 'created': created, 'ambiguous': ambiguous}


def link_nba_identity(espn_id: str, nba_id: str, evidence: str) -> PlayerIdentity:
    if not evidence.strip():
        raise ValueError('A source or manual verification note is required')
    identity = PlayerIdentity.query.filter_by(espn_id=str(espn_id)).first()
    if identity is None:
        raise ValueError(f'ESPN player {espn_id} has not been indexed')
    linked = PlayerIdentity.query.filter_by(nba_id=str(nba_id)).first()
    if linked is not None and linked.espn_id != identity.espn_id:
        raise ValueError(f'NBA player {nba_id} is already linked to ESPN {linked.espn_id}')
    if identity.status == 'ambiguous':
        raise ValueError('Ambiguous ESPN identity requires source review before linking')
    identity.nba_id = str(nba_id)
    identity.status = 'linked'
    identity.evidence = evidence.strip()[:160]
    identity.checked_at = datetime.now(timezone.utc)
    db.session.commit()
    return identity


def verified_nba_id(player_name: str) -> str | None:
    identities = PlayerIdentity.query.filter_by(
        name_key=_player_name_key(player_name),
    ).all()
    if len(identities) != 1:
        return None
    identity = identities[0]
    return identity.nba_id if identity.status == 'linked' else None


def _unique_espn_id(player_name: str, as_of: date) -> str | None:
    name_key = _player_name_key(player_name)
    if not name_key:
        return None
    identities = PlayerIdentity.query.filter_by(name_key=name_key).all()
    if identities:
        if len({row.espn_id for row in identities}) != 1:
            return None
        verified = {row.espn_id for row in identities
                    if row.status in ('source_verified', 'linked')}
        return next(iter(verified)) if len(verified) == 1 else None

    recent_ids = (db.session.query(HistoricalGameLog.player_id)
                  .filter_by(sport='nba')
                  .filter(func.lower(HistoricalGameLog.player_name) == player_name.lower())
                  .filter(HistoricalGameLog.game_date < as_of)
                  .filter(HistoricalGameLog.game_date >= as_of - timedelta(days=500))
                  .filter(~HistoricalGameLog.game_id.like('00%'))
                  .distinct().all())
    return recent_ids[0][0] if len(recent_ids) == 1 else None


def get_verified_history(player_name: str, *, as_of: date, limit: int = 82) -> list:
    espn_id = _unique_espn_id(player_name, as_of)
    if espn_id is None:
        return []
    rows = (HistoricalGameLog.query
            .filter_by(sport='nba', player_id=espn_id)
            .filter(HistoricalGameLog.game_date < as_of)
            .order_by(HistoricalGameLog.game_date.desc())
            .limit(limit).all())
    logs = []
    for row in rows:
        stats = row.stats or {}
        if not (stats.get('minutes') or 0) > 0:
            continue
        logs.append(SimpleNamespace(
            player_id=espn_id,
            player_name=row.player_name,
            team_abbr=row.team_abbr,
            game_date=row.game_date,
            matchup=f'{row.team_abbr} vs. {row.opp_abbr}'
                    if row.home_away == 'HOME' else f'{row.team_abbr} @ {row.opp_abbr}',
            home_away=(row.home_away or '').lower(),
            win_loss=row.win_loss,
            **{key: stats.get(key) or 0.0 for key in _STATS},
        ))
    return logs
