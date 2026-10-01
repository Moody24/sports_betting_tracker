"""Read SportsDataverse ESPN release assets with their source dates intact."""

from collections import defaultdict
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from app import db
from app.models import DataFetchReceipt, InjuryReport
from app.services.data_receipts import record_fetch
from app.services.espn_mapping import NBA_TEAMS, normalize_abbr, season_for_date
from app.utils.time_helpers import et_today


_RELEASES = {
    'team': ('espn_nba_team_boxscores', 'team_box_{year}.parquet'),
    'injuries': ('espn_nba_injuries', 'injuries_{year}.parquet'),
}


def load_release(kind: str, year: int, *, from_dir: str | None = None):
    tag, filename = _RELEASES[kind]
    name = filename.format(year=year)
    if from_dir:
        return pd.read_parquet(Path(from_dir) / name), f'local:{name}'
    url = (f'https://github.com/sportsdataverse/sportsdataverse-data/'
           f'releases/download/{tag}/{name}')
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    version = response.headers.get('ETag') or response.headers.get('Last-Modified') or tag
    return pd.read_parquet(BytesIO(response.content)), version[:120]


def team_defense_from_release(frame, as_of: date, *, min_games: int = 1) -> list[dict]:
    required_stats = (
        'field_goals_attempted', 'offensive_rebounds', 'total_turnovers',
        'free_throws_attempted', 'team_score', 'total_rebounds', 'assists',
        'three_point_field_goals_made', 'steals', 'blocks',
    )
    played = frame[(frame['season_type'] == 2) & (frame['game_date'] < as_of)]
    boxes = {
        (str(row['game_id']), str(row['team_id'])): row
        for row in played.to_dict('records')
    }
    allowed = defaultdict(list)
    for row in boxes.values():
        opponent = boxes.get((str(row['game_id']), str(row['opponent_team_id'])))
        if opponent is None:
            continue
        if (normalize_abbr(str(row['team_abbreviation'])) not in NBA_TEAMS
                or normalize_abbr(str(opponent['team_abbreviation'])) not in NBA_TEAMS):
            continue
        if any(pd.isna(box[field]) for box in (row, opponent)
               for field in required_stats):
            continue
        own_possessions = (float(row['field_goals_attempted'])
                           - float(row['offensive_rebounds'])
                           + float(row['total_turnovers'])
                           + 0.44 * float(row['free_throws_attempted']))
        opp_possessions = (float(opponent['field_goals_attempted'])
                           - float(opponent['offensive_rebounds'])
                           + float(opponent['total_turnovers'])
                           + 0.44 * float(opponent['free_throws_attempted']))
        possessions = (own_possessions + opp_possessions) / 2
        if possessions > 0:
            allowed[str(row['team_id'])].append((row, opponent, possessions))

    result = []
    for team_id, games in allowed.items():
        if len(games) < min_games:
            continue
        count = len(games)

        def opponent_avg(field):
            return sum(float(opponent[field]) for _, opponent, _ in games) / count

        pace = sum(possessions for _, _, possessions in games) / count
        team = games[-1][0]
        points = opponent_avg('team_score')
        result.append({
            'team_id': team_id,
            'team_name': team['team_display_name'],
            'team_abbr': normalize_abbr(team['team_abbreviation']),
            'opp_pts_pg': points,
            'opp_reb_pg': opponent_avg('total_rebounds'),
            'opp_ast_pg': opponent_avg('assists'),
            'opp_3pm_pg': opponent_avg('three_point_field_goals_made'),
            'opp_stl_pg': opponent_avg('steals'),
            'opp_blk_pg': opponent_avg('blocks'),
            'opp_tov_pg': opponent_avg('total_turnovers'),
            'pace': pace,
            'def_rating': points / pace * 100,
            'sample_games': count,
            'observed_through': max(row['game_date'] for row, _, _ in games),
            'source': 'sportsdataverse_team_box',
        })
    return result


def import_release_defense(year: int, *, from_dir: str | None = None) -> dict:
    from app.services.matchup_service import save_team_defense

    try:
        frame, version = load_release('team', year, from_dir=from_dir)
    except (requests.RequestException, OSError, ValueError) as exc:
        record_fetch('nba_team_defense', 'failed', detail=str(exc))
        db.session.commit()
        return {'teams': 0, 'observed_through': None}
    today = et_today()
    current_end_year = int(season_for_date(today)[:4]) + 1
    source = ('sportsdataverse_team_prior' if year < current_end_year
              else 'sportsdataverse_team_box')
    try:
        team_stats = team_defense_from_release(
            frame, today, min_games=1 if year < current_end_year else 5,
        )
    except (KeyError, TypeError, ValueError) as exc:
        record_fetch('nba_team_defense', 'failed', source_version=version,
                     detail=f'Invalid team release: {exc}')
        db.session.commit()
        return {'teams': 0, 'observed_through': None}
    if not team_stats:
        record_fetch('nba_team_defense', 'failed', source_version=version,
                     detail='No complete regular-season team games in release')
        db.session.commit()
        return {'teams': 0, 'observed_through': None}
    for team in team_stats:
        team['source'] = source
    save_team_defense(team_stats, today, source_version=version)
    return {
        'teams': len(team_stats),
        'observed_through': max(item['observed_through'] for item in team_stats),
    }


def import_release_injuries(year: int, *, from_dir: str | None = None) -> dict:
    from app.services.context_service import _normalize_injury_status, _INJURY_CACHE

    try:
        frame, version = load_release('injuries', year, from_dir=from_dir)
    except (requests.RequestException, OSError, ValueError) as exc:
        record_fetch('espn_injuries', 'failed', detail=str(exc))
        db.session.commit()
        return {'date': None, 'rows': 0}
    if frame.empty or 'as_of_date' not in frame or frame['as_of_date'].dropna().empty:
        record_fetch('espn_injuries', 'failed', source_version=version,
                     detail='Injury release has no dated rows')
        db.session.commit()
        return {'date': None, 'rows': 0}
    report_date = max(frame['as_of_date'].dropna())
    latest = frame[frame['as_of_date'] == report_date]
    today = et_today()
    direct_is_current = report_date == today and db.session.query(
        DataFetchReceipt.id,
    ).filter_by(
        feed='espn_injuries', status='success', data_through=today,
        source_version='espn_site',
    ).first() is not None
    release_rows = latest.to_dict('records')
    release_statuses = {
        (str(row.get('athlete_display_name') or '').strip().lower(),
         _normalize_injury_status(str(row.get('status') or '')))
        for row in release_rows if row.get('athlete_display_name')
    }
    if direct_is_current:
        direct_statuses = {
            (row.player_name.strip().lower(), row.status)
            for row in InjuryReport.query.filter_by(
                date_reported=today, source='espn',
            ).all()
        }
        if direct_statuses != release_statuses:
            record_fetch('espn_injuries', 'conflict', len(latest),
                         data_through=report_date, source_version=version,
                         detail='Direct and release injury reports disagree')
            db.session.commit()
            return {'date': report_date, 'rows': 0, 'status': 'conflict'}
        record_fetch('espn_injuries_release', 'success', len(latest),
                     data_through=report_date, source_version=version)
        record_fetch('espn_injuries', 'success', len(latest),
                     data_through=report_date, source_version='espn_site')
        db.session.commit()
        return {'date': report_date, 'rows': len(latest), 'status': 'matched'}

    InjuryReport.query.filter_by(
        date_reported=report_date, source='sportsdataverse_espn',
    ).delete()
    for row in release_rows:
        name = row.get('athlete_display_name')
        if not name:
            continue
        db.session.add(InjuryReport(
            player_name=name,
            team=row.get('team_display_name'),
            status=_normalize_injury_status(str(row.get('status') or '')),
            detail=(str(row['short_comment'])[:300]
                    if pd.notna(row.get('short_comment')) else ''),
            date_reported=report_date,
            source='sportsdataverse_espn',
        ))
    record_fetch('espn_injuries', 'success', len(latest),
                 data_through=report_date, source_version=version)
    db.session.commit()
    _INJURY_CACHE.clear()
    return {'date': report_date, 'rows': len(latest), 'status': 'imported'}
