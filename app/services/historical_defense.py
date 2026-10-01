"""Observed opponent allowances from permanent ESPN player box scores."""

from collections import defaultdict
from datetime import date

from app.models import HistoricalGameLog
from app.services.espn_mapping import NBA_TEAMS, season_for_date


_BOX_STATS = ('pts', 'reb', 'ast', 'fg3m', 'stl', 'blk', 'tov', 'fga', 'fta')


def _team_catalog() -> dict:
    from nba_api.stats.static import teams

    return {team['abbreviation']: team for team in teams.get_teams()}


def _season_before(season: str) -> str:
    year = int(season[:4]) - 1
    return f'{year}-{str(year + 1)[-2:]}'


def observed_team_defense(as_of: date, *, min_current_games: int = 5) -> list[dict]:
    current = season_for_date(as_of)
    for season, source in (
        (current, 'espn_boxscore_current'),
        (_season_before(current), 'espn_boxscore_prior'),
    ):
        rows = (HistoricalGameLog.query.filter_by(sport='nba', season=season)
                .filter(HistoricalGameLog.game_date < as_of)
                .filter(~HistoricalGameLog.game_id.like('00%'))
                .all())
        if not rows:
            continue

        boxes = defaultdict(lambda: defaultdict(float))
        dates = {}
        for row in rows:
            if row.team_abbr not in NBA_TEAMS or row.opp_abbr not in NBA_TEAMS:
                continue
            key = (row.game_id, row.team_abbr, row.opp_abbr)
            for stat in _BOX_STATS:
                boxes[key][stat] += float((row.stats or {}).get(stat) or 0)
            dates[row.game_id] = row.game_date

        allowed = defaultdict(list)
        for (game_id, team, opponent), stats in boxes.items():
            other = boxes.get((game_id, opponent, team))
            if other and stats['pts'] > 0 and other['pts'] > 0:
                possessions = (
                    stats['fga'] + 0.44 * stats['fta'] + stats['tov']
                    + other['fga'] + 0.44 * other['fta'] + other['tov']
                ) / 2
                allowed[opponent].append((dates[game_id], stats, possessions))

        catalog = _team_catalog()
        result = []
        for abbr, games in allowed.items():
            if source == 'espn_boxscore_current' and len(games) < min_current_games:
                continue
            team = catalog.get(abbr)
            if team is None:
                continue
            count = len(games)
            def avg(key):
                return sum(stats[key] for _, stats, _ in games) / count
            possessions = sum(possessions for _, _, possessions in games) / count
            result.append({
                'team_id': str(team['id']),
                'team_name': team['full_name'],
                'team_abbr': abbr,
                'opp_pts_pg': avg('pts'),
                'opp_reb_pg': avg('reb'),
                'opp_ast_pg': avg('ast'),
                'opp_3pm_pg': avg('fg3m'),
                'opp_stl_pg': avg('stl'),
                'opp_blk_pg': avg('blk'),
                'opp_tov_pg': avg('tov'),
                'pace': possessions,
                'def_rating': avg('pts') / possessions * 100 if possessions else None,
                'sample_games': count,
                'observed_through': max(game_date for game_date, _, _ in games),
                'source': source,
            })
        if result:
            if source == 'espn_boxscore_current' and len(result) < len(catalog):
                prior = observed_team_defense_prior(as_of)
                known = {item['team_abbr'] for item in result}
                result.extend(
                    {**item, 'source': 'espn_boxscore_prior'}
                    for item in prior if item['team_abbr'] not in known
                )
            return result
    return []


def observed_team_defense_prior(as_of: date) -> list[dict]:
    start_year = int(season_for_date(as_of)[:4])
    prior_date = date(start_year, 9, 30)
    return [
        team for team in observed_team_defense(prior_date, min_current_games=0)
        if team['observed_through'] >= date(start_year - 1, 10, 1)
    ]
