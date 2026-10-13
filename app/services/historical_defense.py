"""Observed opponent allowances from permanent ESPN player box scores."""

from collections import defaultdict, deque
from datetime import date
from itertools import groupby

from app.models import HistoricalGameLog
from app.services.espn_mapping import NBA_TEAMS, season_for_date


_BOX_STATS = ('pts', 'reb', 'ast', 'fg3m', 'stl', 'blk', 'tov', 'fga', 'fta')


def rolling_defense_by_game(logs, *, min_prior_games: int = 5,
                            window: int = 10) -> dict[tuple[date, str], dict]:
    """Opponent profiles available before each completed game."""
    from app.services.ml_feature_builder import extract_opp_abbr

    boxes = defaultdict(lambda: defaultdict(float))
    players = defaultdict(set)
    opponents = {}
    for row in logs:
        team = (row.team_abbr or '').strip().upper()
        opponent = extract_opp_abbr(row.matchup or '')
        if not row.game_date or team not in NBA_TEAMS or opponent not in NBA_TEAMS:
            continue
        game_id = str(getattr(row, '_historical_game_id', '') or '')
        key = (row.game_date, game_id, team)
        for stat in _BOX_STATS:
            boxes[key][stat] += float(getattr(row, stat, 0) or 0)
        players[key].add(str(row.player_id))
        opponents[key] = opponent

    completed = []
    for key, stats in boxes.items():
        game_date, game_id, team = key
        opponent = opponents[key]
        opposing_key = (game_date, game_id, opponent)
        opposing = boxes.get(opposing_key)
        if (not opposing or len(players[key]) < 8
                or len(players[opposing_key]) < 8
                or stats['pts'] <= 0 or opposing['pts'] <= 0):
            continue
        pace = (stats['fga'] + 0.44 * stats['fta'] + stats['tov']
                + opposing['fga'] + 0.44 * opposing['fta'] + opposing['tov']) / 2
        if pace <= 0:
            continue
        completed.append((game_date, team, opposing, pace))

    history = defaultdict(lambda: deque(maxlen=window))
    profiles = {}
    for game_date, games in groupby(sorted(completed, key=lambda game: game[0]),
                                    key=lambda game: game[0]):
        day = list(games)
        for _, team, _, _ in day:
            prior = history[team]
            if len(prior) < min_prior_games:
                continue
            count = len(prior)
            fields = {
                'pts': 'opp_pts_pg', 'reb': 'opp_reb_pg',
                'ast': 'opp_ast_pg', 'fg3m': 'opp_3pm_pg',
                'stl': 'opp_stl_pg', 'blk': 'opp_blk_pg',
            }
            profile = {
                field: sum(game[0][stat] for game in prior) / count
                for stat, field in fields.items()
            }
            profile['pace'] = sum(game[1] for game in prior) / count
            profile['def_rating'] = profile['opp_pts_pg'] / profile['pace'] * 100
            profiles[(game_date, team)] = profile
        for _, team, allowed, pace in day:
            history[team].append((allowed, pace))
    return profiles


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
