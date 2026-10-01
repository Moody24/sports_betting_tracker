"""Explore pregame matchup context against historical player box scores."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.espn_mapping import NBA_TEAMS
from app.services.scenario_dimensions import load_frame, load_odds_frame


PLAYER_STATS = ('pts', 'reb', 'ast', 'fg3m', 'fg3a', 'fga', 'fta',
                'stl', 'blk', 'tov', 'minutes')
TEAM_STATS = ('pts', 'reb', 'ast', 'fg3a', 'fga', 'fta', 'stl', 'blk', 'tov')
DEFENSE_METRICS = (
    'three_rate_allowed', 'fta_rate_allowed', 'assists_allowed',
    'rebounds_allowed', 'steals_forced', 'blocks_recorded',
    'tempo_proxy', 'points_allowed',
)
MIN_PRIOR_GAMES = 10


def _previous_ten(grouped, column: str) -> pd.Series:
    return grouped[column].transform(
        lambda values: values.shift().rolling(MIN_PRIOR_GAMES, min_periods=MIN_PRIOR_GAMES).mean()
    )


def prepare_team_games(player_rows: pd.DataFrame) -> pd.DataFrame:
    rows = player_rows.copy()
    rows['game_date'] = pd.to_datetime(rows['game_date'])
    for stat in TEAM_STATS:
        rows[stat] = pd.to_numeric(rows[stat], errors='coerce').fillna(0)

    teams = rows.groupby(
        ['game_id', 'season', 'game_date', 'team_abbr', 'opp_abbr'],
        as_index=False,
    ).agg(
        **{stat: (stat, 'sum') for stat in TEAM_STATS},
        team_score=('team_score', 'first'),
        home_away=('home_away', 'first'),
        players=('player_id', 'nunique'),
    )
    opponents = teams[
        ['game_id', 'team_abbr', 'team_score', 'players', *TEAM_STATS]
    ].rename(columns={
        'team_abbr': 'opp_abbr', 'team_score': 'opponent_score',
        'players': 'opponent_players',
        **{stat: f'opponent_{stat}' for stat in TEAM_STATS},
    })
    teams = teams.merge(opponents, on=['game_id', 'opp_abbr'], validate='one_to_one')
    teams = teams[(teams['players'] >= 8) & (teams['opponent_players'] >= 8)].copy()
    teams['three_rate_allowed'] = teams['opponent_fg3a'] / teams['opponent_fga'].replace(0, np.nan)
    teams['fta_rate_allowed'] = teams['opponent_fta'] / teams['opponent_fga'].replace(0, np.nan)
    teams['assists_allowed'] = teams['opponent_ast']
    teams['rebounds_allowed'] = teams['opponent_reb']
    teams['points_allowed'] = teams['opponent_score']
    teams['steals_forced'] = teams['stl']
    teams['blocks_recorded'] = teams['blk']
    teams['tempo_proxy'] = (
        teams['fga'] + 0.44 * teams['fta'] + teams['tov']
        + teams['opponent_fga'] + 0.44 * teams['opponent_fta']
        + teams['opponent_tov']
    ) / 2

    teams = teams.sort_values(['season', 'team_abbr', 'game_date', 'game_id'])
    grouped = teams.groupby(['season', 'team_abbr'], sort=False)
    for metric in DEFENSE_METRICS:
        teams[f'prior_{metric}'] = _previous_ten(grouped, metric)
    teams['rest_days'] = grouped['game_date'].diff().dt.days - 1
    teams['prior_team_meetings'] = teams.groupby(
        ['season', 'team_abbr', 'opp_abbr'], sort=False,
    ).cumcount()
    return teams


def prepare_player_games(player_rows: pd.DataFrame, teams: pd.DataFrame,
                         game_odds: pd.DataFrame) -> pd.DataFrame:
    rows = player_rows.copy()
    rows['game_date'] = pd.to_datetime(rows['game_date'])
    for stat in PLAYER_STATS:
        rows[stat] = pd.to_numeric(rows[stat], errors='coerce').fillna(0)
    rows = rows[rows['minutes'] > 0].copy()
    rows['pra'] = rows['pts'] + rows['reb'] + rows['ast']
    rows = rows.sort_values(['season', 'player_id', 'game_date', 'game_id'])
    grouped = rows.groupby(['season', 'player_id'], sort=False)
    for stat in (*PLAYER_STATS, 'pra'):
        rows[f'prior_{stat}'] = _previous_ten(grouped, stat)
    rows['prior_player_meetings'] = rows.groupby(
        ['season', 'player_id', 'opp_abbr'], sort=False,
    ).cumcount()

    context = teams[
        ['game_id', 'team_abbr', 'rest_days', 'prior_team_meetings',
         *(f'prior_{metric}' for metric in DEFENSE_METRICS)]
    ].rename(columns={
        'team_abbr': 'opp_abbr',
        **{f'prior_{metric}': f'def_{metric}' for metric in DEFENSE_METRICS},
    })
    rows = rows.merge(context, on=['game_id', 'opp_abbr'], validate='many_to_one')

    if not game_odds.empty:
        home = teams[teams['home_away'] == 'HOME'][
            ['game_id', 'team_abbr', 'opp_abbr']
        ].rename(columns={'team_abbr': 'home_abbr', 'opp_abbr': 'away_abbr'})
        odds = game_odds.copy()
        odds['game_date'] = pd.to_datetime(odds['game_date'])
        dates = teams[teams['home_away'] == 'HOME'][['game_id', 'game_date']]
        home = home.merge(dates, on='game_id', validate='one_to_one')
        home = home.merge(
            odds[['game_date', 'home_abbr', 'away_abbr', 'total', 'spread']],
            on=['game_date', 'home_abbr', 'away_abbr'], how='left',
            validate='one_to_one',
        )
        rows = rows.merge(home[['game_id', 'total', 'spread']],
                          on='game_id', how='left', validate='many_to_one')
    else:
        rows['total'] = np.nan
        rows['spread'] = np.nan

    rows['final_total'] = rows['team_score'] + rows['opp_score']
    rows['final_margin'] = (rows['team_score'] - rows['opp_score']).abs()
    rows['minutes_residual'] = rows['minutes'] - rows['prior_minutes']
    for stat in ('pts', 'reb', 'ast', 'fg3m', 'tov', 'pra'):
        rows[f'{stat}_residual'] = rows[stat] - rows[f'prior_{stat}']
        rows[f'{stat}_above_baseline'] = rows[stat] > rows[f'prior_{stat}']
        rows[f'{stat}_below_baseline'] = rows[stat] < rows[f'prior_{stat}']
    return rows


def training_thresholds(teams: pd.DataFrame, odds: pd.DataFrame,
                        discovery_seasons: tuple[str, ...]) -> dict:
    reference = teams[teams['season'].isin(discovery_seasons)]
    thresholds = {}
    for metric in DEFENSE_METRICS:
        prior = reference[f'prior_{metric}'].dropna()
        thresholds[metric] = [float(prior.quantile(q)) for q in (1 / 3, 2 / 3)]
    if not odds.empty:
        game_odds = odds.copy()
        game_odds['game_date'] = pd.to_datetime(game_odds['game_date'])
        start_year = game_odds['game_date'].dt.year.where(
            game_odds['game_date'].dt.month >= 10,
            game_odds['game_date'].dt.year - 1,
        )
        reference_totals = game_odds.loc[
            start_year.isin(int(season[:4]) for season in discovery_seasons),
            'total',
        ].dropna()
        if not reference_totals.empty:
            thresholds['closing_total'] = [
                float(reference_totals.quantile(q)) for q in (1 / 3, 2 / 3)
            ]
    return thresholds


def _cluster_interval(high: pd.DataFrame, low: pd.DataFrame, seed: int,
                      iterations: int) -> tuple[float, float]:
    combined = pd.concat([high.assign(arm='high'), low.assign(arm='low')])
    by_game = combined.groupby(['game_id', 'arm']).agg(
        total=('residual', 'sum'), count=('residual', 'size'),
    ).unstack('arm', fill_value=0)
    arrays = [by_game[(field, arm)].to_numpy()
              for field in ('total', 'count') for arm in ('high', 'low')]
    draws = np.random.default_rng(seed).integers(
        len(by_game), size=(iterations, len(by_game)),
    )
    high_sum, low_sum, high_count, low_count = (
        values[draws].sum(axis=1) for values in arrays
    )
    effects = high_sum / np.maximum(high_count, 1) - low_sum / np.maximum(low_count, 1)
    return tuple(float(value) for value in np.quantile(effects, [0.025, 0.975]))


def compare_arms(rows: pd.DataFrame, stat: str, high_mask: pd.Series,
                 low_mask: pd.Series, *, seed: int = 20261001,
                 iterations: int = 400) -> dict:
    valid = rows[f'prior_{stat}'].notna()
    high = rows[valid & high_mask][['game_id', 'season', 'player_id', f'{stat}_residual',
                                    f'{stat}_above_baseline',
                                    f'{stat}_below_baseline']].rename(columns={
        f'{stat}_residual': 'residual',
        f'{stat}_above_baseline': 'above',
        f'{stat}_below_baseline': 'below',
    })
    low = rows[valid & low_mask][['game_id', 'season', 'player_id', f'{stat}_residual',
                                  f'{stat}_above_baseline',
                                  f'{stat}_below_baseline']].rename(columns={
        f'{stat}_residual': 'residual',
        f'{stat}_above_baseline': 'above',
        f'{stat}_below_baseline': 'below',
    })
    result = {
        'high_n': len(high), 'low_n': len(low),
        'high_games': high['game_id'].nunique(),
        'low_games': low['game_id'].nunique(),
        'paired_players': 0, 'paired_effect': None,
    }
    if min(len(high), len(low)) < 100 or min(result['high_games'], result['low_games']) < 30:
        result.update(effect=None, ci_low=None, ci_high=None,
                      high_above=None, low_above=None,
                      high_below=None, low_below=None)
        return result
    result.update(
        effect=float(high['residual'].mean() - low['residual'].mean()),
        high_above=float(high['above'].mean()),
        low_above=float(low['above'].mean()),
        high_below=float(high['below'].mean()),
        low_below=float(low['below'].mean()),
    )
    player_means = pd.concat([
        high.groupby(['season', 'player_id'])['residual'].mean().rename('high'),
        low.groupby(['season', 'player_id'])['residual'].mean().rename('low'),
    ], axis=1).dropna()
    result['paired_players'] = len(player_means)
    if not player_means.empty:
        result['paired_effect'] = float((player_means['high'] - player_means['low']).mean())
    result['ci_low'], result['ci_high'] = _cluster_interval(
        high, low, seed, iterations,
    )
    return result


def signal_results(rows: pd.DataFrame, thresholds: dict,
                   discovery_seasons: tuple[str, ...],
                   validation_season: str, iterations: int = 400) -> pd.DataFrame:
    signals = (
        ('perimeter_3pm', 'fg3m', 'three_rate_allowed',
         lambda f: (f['prior_fg3a'] / f['prior_fga'].replace(0, np.nan) >= 0.45)
                   & (f['prior_fga'] >= 5)),
        ('playmaker_assists', 'ast', 'assists_allowed',
         lambda f: f['prior_ast'] >= 5),
        ('rebounder_rebounds', 'reb', 'rebounds_allowed',
         lambda f: f['prior_reb'] >= 8),
        ('foul_drawing_points', 'pts', 'fta_rate_allowed',
         lambda f: (f['prior_fta'] / f['prior_fga'].replace(0, np.nan) >= 0.25)
                   & (f['prior_fga'] >= 8)),
        ('pressure_turnovers', 'tov', 'steals_forced',
         lambda f: (f['prior_ast'] >= 4) & (f['prior_tov'] >= 2)),
        ('shot_blocking_points', 'pts', 'blocks_recorded',
         lambda f: (f['prior_fga'] - f['prior_fg3a'] >= 7)
                   & (f['prior_pts'] >= 12)),
        ('tempo_points', 'pts', 'tempo_proxy',
         lambda f: f['prior_pts'] >= 12),
        ('closing_total_points', 'pts', 'closing_total',
         lambda f: f['prior_pts'] >= 18),
    )
    results = []
    for name, stat, metric, archetype in signals:
        if metric not in thresholds:
            continue
        value_column = 'total' if metric == 'closing_total' else f'def_{metric}'
        low_cut, high_cut = thresholds[metric]
        for scope, seasons in (
            ('discovery', discovery_seasons), ('validation', (validation_season,)),
        ):
            sample = rows[rows['season'].isin(seasons)].copy()
            eligible = archetype(sample)
            comparison = compare_arms(
                sample, stat,
                eligible & (sample[value_column] >= high_cut),
                eligible & (sample[value_column] <= low_cut),
                iterations=iterations,
            )
            results.append(dict(signal=name, scope=scope, stat=stat,
                                profile=metric, low_cut=low_cut,
                                high_cut=high_cut, **comparison))

    for scope, seasons in (
        ('discovery', discovery_seasons), ('validation', (validation_season,)),
    ):
        sample = rows[rows['season'].isin(seasons)]
        scorer = sample['prior_pts'] >= 18
        comparisons = (
            ('team_back_to_back', scorer & (sample['rest_days'] == 0),
             scorer & (sample['rest_days'] >= 2)),
            ('third_opponent_meeting', scorer & (sample['prior_player_meetings'] >= 2),
             scorer & (sample['prior_player_meetings'] == 0)),
        )
        for name, high_mask, low_mask in comparisons:
            comparison = compare_arms(
                sample, 'pts', high_mask, low_mask, iterations=iterations,
            )
            results.append(dict(signal=name, scope=scope, stat='pts',
                                profile='schedule', low_cut=None,
                                high_cut=None, **comparison))
    return pd.DataFrame(results)


def _markdown_report(rows: pd.DataFrame, teams: pd.DataFrame,
                     team_pairs: pd.DataFrame, repeated: pd.DataFrame,
                     results: pd.DataFrame,
                     thresholds: dict,
                     discovery_seasons: tuple[str, ...],
                     validation_season: str) -> str:
    observed = rows[rows['season'] == validation_season]
    repeat = observed[observed['prior_player_meetings'] >= 2]
    joined = observed[observed['total'].notna()]
    scorer = observed[observed['prior_pts'] >= 18]
    player_score_corr = scorer['pts_residual'].corr(scorer['final_total'])
    other_score_corr = scorer['pts_residual'].corr(
        scorer['final_total'] - scorer['pts']
    )
    closing_total_corr = joined['pts_residual'].corr(joined['total'])
    close_games = scorer[scorer['final_margin'] <= 5]
    blowouts = scorer[scorer['final_margin'] >= 15]

    lines = [
        f'# NBA matchup pattern screen — {datetime.now(ZoneInfo("America/New_York")).date().isoformat()}', '',
        f'Discovery seasons: {", ".join(discovery_seasons)}. '
        f'Later-season check: {validation_season}. The hypotheses were refined '
        'after inspecting an initial pass, so this is exploratory rather than '
        'a preregistered holdout.',
        f'Historical source: {len(rows):,} played player-games; '
        f'{rows.game_id.nunique():,} games after complete-box and prior-history filters. '
        f'{len(teams):,} complete team-game rows before prior-history filters.',
        f'Later season: {len(observed):,} player-games; '
        f'{len(repeat):,} have at least two earlier same-season meetings with the opponent; '
        f'{len(joined):,} join a historical closing game total.', '',
        'A result above a player\'s prior-ten-game mean is a **statistical baseline beat**, '
        'not a bookmaker prop over. No historical player-prop offers were available for this run. '
        'Effects are differences in mean box-score residuals (high profile minus low profile). '
        'Intervals resample games so players in the same game move together. '
        'These are exploratory associations, not causal effects or betting edges. '
        'Intervals are unadjusted for the ten screened patterns. The same-player '
        'column is descriptive and has no interval. Above/below-baseline rates '
        'are in the CSV; exact ties count as neither.', '',
        '| Pattern | Discovery effect | Later-season effect [95% interval] | Same-player effect | Above prior mean, high/low | Later-season n high/low |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for name in results['signal'].drop_duplicates():
        pair = results[results['signal'] == name].set_index('scope')
        discovery, validation = pair.loc['discovery'], pair.loc['validation']
        def effect(row):
            if pd.isna(row['effect']):
                return 'insufficient sample'
            return f"{row['effect']:+.2f}"
        interval = (
            f"{effect(validation)} [{validation['ci_low']:+.2f}, {validation['ci_high']:+.2f}]"
            if pd.notna(validation['effect']) else 'insufficient sample'
        )
        paired = (
            f"{validation['paired_effect']:+.2f} ({validation['paired_players']:,})"
            if pd.notna(validation['paired_effect']) else 'insufficient pairs'
        )
        above = (
            f"{validation['high_above']:.1%}/{validation['low_above']:.1%}"
            if pd.notna(validation['high_above']) else 'insufficient sample'
        )
        lines.append(
            f"| {name.replace('_', ' ')} | {effect(discovery)} | {interval} | "
            f"{paired} | {above} | {validation['high_n']:,}/{validation['low_n']:,} |"
        )
    common_pairs = ', '.join(
        f"{row.pair} ({row.games})" for row in team_pairs.head(5).itertuples()
    )
    repeated_players = ', '.join(
        f"{row.player_name} vs {row.opp_abbr} ({row.games})"
        for row in repeated[(repeated['games'] >= 5)
                            & (repeated['mean_pts'] >= 15)].head(5).itertuples()
    )
    lines.extend([
        '', f'Most frequent team pairings in {validation_season}: {common_pairs}. '
        'These are observed meetings, not a guarantee of future schedule frequency. '
        'The [NBA Cup schedule](https://www.nba.com/news/2026-27-nba-regular-season-schedule) '
        'can add matchups beyond the initially assigned regular-season games.',
        f'Repeated player-opponent examples: {repeated_players}. Each player-specific '
        'sample is too small to establish a prop edge.',
        '', 'Final scores are known only **after** the game. In validation games, '
        f'prior-adjusted points correlate {player_score_corr:+.3f} with the final game total; '
        f'after removing the player\'s own points from that total, the correlation is '
        f'{other_score_corr:+.3f}. The historical closing game total has correlation '
        f'{closing_total_corr:+.3f} on {len(joined):,} joined rows. '
        'The closing-line source lacks a bookmaker and exact observation time, so '
        'it cannot establish what was offered at an earlier decision point. '
        'The final-score relationships are descriptive and cannot be used as pregame inputs.',
        f'In games decided by 5 or fewer points, high-volume scorers averaged '
        f'{close_games["minutes_residual"].mean():+.2f} minutes and '
        f'{close_games["pts_residual"].mean():+.2f} points versus their prior-ten-game means '
        f'(n={len(close_games):,}); in 15+ point games those figures were '
        f'{blowouts["minutes_residual"].mean():+.2f} minutes and '
        f'{blowouts["pts_residual"].mean():+.2f} points (n={len(blowouts):,}). '
        'The margin is observed after the game, so this describes game script rather '
        'than a usable pregame forecast.',
        '', 'The profiles are observed box-score allowances and team steals/blocks, '
        'not identified man, zone, drop, switch, or hedge schemes. A real coverage '
        'study needs possession-level tracking or tagged video, opponent defender '
        'assignments, and timestamped quotes. Player steals and blocks alone do not '
        'establish on-ball defensive impact.',
        '', 'Profile cutoffs were fixed from discovery team-games:',
        '', '| Profile | Low at or below | High at or above |',
        '|---|---:|---:|',
    ])
    for metric, (low, high) in thresholds.items():
        lines.append(f'| {metric.replace("_", " ")} | {low:.2f} | {high:.2f} |')
    lines.extend([
        '', 'The source is ESPN player box scores via SportsDataverse releases. '
        'For richer defensive measurement, the NBA publishes '
        '[team opponent shooting](https://www.nba.com/stats/teams/opponent-shots-general) '
        'and [player defense dashboards](https://www.nba.com/stats/players/defense-dash-overall); '
        'these are not yet joined point-in-time to this research. '
        '[ESPN play-by-play releases](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_pbp) '
        'may add possessions and shot context, but do not by themselves identify coverage.',
        'No signal from this screen is wired into automatic recommendations.',
    ])
    return '\n'.join(lines) + '\n'


def run(output_dir: Path, *, discovery_seasons: tuple[str, ...] =
        ('2023-24', '2024-25'), validation_season: str = '2025-26',
        iterations: int = 400) -> dict:
    raw = load_frame('nba', seasons=[*discovery_seasons, validation_season])
    raw = raw[
        raw['team_abbr'].isin(NBA_TEAMS)
        & raw['opp_abbr'].isin(NBA_TEAMS)
        & ~raw['game_id'].astype(str).str.startswith('00')
    ]
    odds = load_odds_frame()
    teams = prepare_team_games(raw)
    rows = prepare_player_games(raw, teams, odds)
    rows = rows[rows['prior_pts'].notna() & rows['def_points_allowed'].notna()].copy()
    thresholds = training_thresholds(teams, odds, discovery_seasons)
    results = signal_results(rows, thresholds, discovery_seasons,
                             validation_season, iterations)

    repeated = (rows[rows['season'] == validation_season]
                .groupby(['player_id', 'player_name', 'team_abbr', 'opp_abbr'],
                         as_index=False)
                .agg(games=('game_id', 'nunique'),
                     mean_pts=('pts', 'mean'),
                     mean_pts_vs_prior=('pts_residual', 'mean'))
                .query('games >= 3')
                .sort_values(['games', 'player_name'], ascending=[False, True]))
    team_pairs = teams[teams['season'] == validation_season].copy()
    team_pairs['pair'] = team_pairs.apply(
        lambda game: '–'.join(sorted((game['team_abbr'], game['opp_abbr']))),
        axis=1,
    )
    team_pairs = (team_pairs.groupby('pair', as_index=False)
                  .agg(games=('game_id', 'nunique'))
                  .sort_values(['games', 'pair'], ascending=[False, True]))
    profiles = (teams[teams['season'] == validation_season]
                .groupby('team_abbr', as_index=False)
                .agg(games=('game_id', 'nunique'),
                     **{metric: (metric, 'mean') for metric in DEFENSE_METRICS}))
    output_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(output_dir / 'signal_screen.csv', index=False)
    profiles.to_csv(output_dir / 'defensive_profiles.csv', index=False)
    repeated.to_csv(output_dir / 'repeat_player_matchups.csv', index=False)
    team_pairs.to_csv(output_dir / 'team_matchup_frequency.csv', index=False)
    report = _markdown_report(rows, teams, team_pairs, repeated, results, thresholds,
                              discovery_seasons, validation_season)
    (output_dir / 'report.md').write_text(report)
    return {'player_games': len(rows), 'games': rows.game_id.nunique(),
            'signals': results.signal.nunique(), 'output_dir': str(output_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=400)
    args = parser.parse_args()

    from dotenv import load_dotenv
    from app import create_app

    load_dotenv('.env')
    app = create_app()
    with app.app_context():
        print(run(args.output_dir, iterations=args.iterations))


if __name__ == '__main__':
    main()
