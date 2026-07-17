"""Context builder + dimension registry for the scenario engine.

Each dimension contributes one `ctx_<name>` bucket column to the
player-game frame. NaN bucket = row excluded from that dimension's splits
(e.g. no odds row, no prior defensive data). Buckets are plain strings.
"""

from __future__ import annotations

import pandas as pd

from app.models import HistoricalGameLog, HistoricalGameOdds

SPLIT_STATS = ('pts', 'reb', 'ast', 'fg3m', 'pra')

DIMENSIONS: dict[str, tuple[str, ...]] = {
    'home_away': ('HOME', 'AWAY'),
    'rest_bucket': ('0', '1', '2', '3+'),
    'role': ('starter', 'bench'),
    'season_segment': ('early', 'mid', 'late'),
    'game_script': ('close', 'normal', 'blowout'),
    'opp_def_tier': ('top10', 'mid', 'bottom10'),
    'pace_tier': ('slow', 'mid', 'fast'),
    'teammate_context': ('full', 'shorthanded'),
    'fav_dog': ('fav_big', 'fav', 'dog', 'dog_big'),
    'total_bucket': ('low', 'mid', 'high'),
}

# Fixed-logic bucketing shared by the historical builder (build_context) and
# the live builder (live_context). One source of truth: edit here, both move.
REST_BINS = [-1, 0, 1, 2, 10_000]
REST_LABELS = ('0', '1', '2', '3+')
SEGMENT_BINS = [9, 12, 14, 17]          # shifted months: Oct..Apr -> 10..16
SEGMENT_LABELS = ('early', 'mid', 'late')
SPREAD_BIG = 7.0


def rest_bucket_label(days_rest: float) -> str:
    for edge, label in zip(REST_BINS[1:], REST_LABELS):
        if days_rest <= edge:
            return label
    return REST_LABELS[-1]


def season_segment_label(d) -> str | None:
    month = d.month
    shifted = month if month >= 9 else month + 12
    if shifted <= SEGMENT_BINS[0] or shifted > SEGMENT_BINS[-1]:
        return None
    for edge, label in zip(SEGMENT_BINS[1:], SEGMENT_LABELS):
        if shifted <= edge:
            return label
    return None


def fav_dog_label(spread: float, team_is_favored: bool) -> str:
    if spread == 0:
        return 'fav'
    big = abs(spread) > SPREAD_BIG
    if team_is_favored:
        return 'fav_big' if big else 'fav'
    return 'dog_big' if big else 'dog'


# Reserved (not implemented): line_move (Plan D), referee_crew (no free data).


def load_frame(sport: str = 'nba',
               seasons: list[str] | None = None) -> pd.DataFrame:
    """One row per player-game with payload stats flattened + pra."""
    q = HistoricalGameLog.query.filter_by(sport=sport)
    if seasons:
        q = q.filter(HistoricalGameLog.season.in_(seasons))
    records = []
    for r in q.all():
        rec = dict(player_id=r.player_id, player_name=r.player_name,
                   game_id=r.game_id, game_date=r.game_date,
                   season=r.season, team_abbr=r.team_abbr,
                   opp_abbr=r.opp_abbr, home_away=r.home_away,
                   starter=bool(r.starter))
        rec.update(r.stats or {})
        records.append(rec)
    df = pd.DataFrame(records)
    if not df.empty:
        df['pra'] = df['pts'] + df['reb'] + df['ast']
    return df


def load_odds_frame() -> pd.DataFrame:
    rows = [dict(game_date=o.game_date, home_abbr=o.home_abbr,
                 away_abbr=o.away_abbr, spread=o.spread,
                 favored=o.favored, total=o.total)
            for o in HistoricalGameOdds.query.all()]
    return pd.DataFrame(rows)


def _safe_qcut(s: pd.Series, labels: tuple[str, ...]) -> pd.Series:
    """qcut ``s`` into ``len(labels)`` quantile bins.

    Small or degenerate groups (too few distinct values, e.g. a single
    game on opening night, or an all-tied group) can't support that many
    bins; ``duplicates='drop'`` then collapses the edges and pandas raises
    ValueError. Return an all-NaN series instead (NaN bucket = row
    excluded from this dimension, the registry's existing convention) so
    one thin season group can't fail the whole build.
    """
    if s.nunique(dropna=True) < len(labels):
        return pd.Series(float('nan'), index=s.index, dtype=object)
    try:
        return pd.qcut(s, len(labels), labels=list(labels),
                       duplicates='drop').astype(object)
    except ValueError:
        return pd.Series(float('nan'), index=s.index, dtype=object)


def _team_games(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team): totals needed for pace/def tiers."""
    g = df.groupby(['game_id', 'game_date', 'season', 'team_abbr',
                    'opp_abbr'], as_index=False).agg(
        fga=('fga', 'sum'), fta=('fta', 'sum'), tov=('tov', 'sum'),
        team_score=('team_score', 'first'), opp_score=('opp_score', 'first'))
    return g


def build_context_pack(df: pd.DataFrame,
                       odds_df: pd.DataFrame | None) -> dict:
    """Build current-season quantile edges and team maps for live context."""
    df = df.copy()
    season = sorted(df['season'].unique())[-1]
    cur = df[df['season'] == season]

    def _qbins(s: pd.Series) -> list | None:
        s = s.dropna()
        if s.nunique() < 3:
            return None
        try:
            _, bins = pd.qcut(s, 3, retbins=True, duplicates='drop')
        except ValueError:
            return None
        return [float(b) for b in bins] if len(bins) == 4 else None

    tg = _team_games(cur)
    tg['poss'] = tg['fga'] + 0.44 * tg['fta'] + tg['tov']
    game_poss = tg.groupby('game_id', as_index=False).agg(poss=('poss', 'sum'))
    pace_bins = _qbins(game_poss['poss'])
    team_poss = (
        tg.merge(game_poss.rename(columns={'poss': 'game_poss'}),
                 on='game_id')
        .groupby('team_abbr')['game_poss']
        .mean()
    )

    allowed = tg.groupby('team_abbr')['opp_score'].mean().dropna()
    pct = allowed.rank(pct=True)
    def_tier = pd.cut(
        pct, bins=[0, 1 / 3, 2 / 3, 1.0001],
        labels=['top10', 'mid', 'bottom10'],
    ).astype(str)

    total_bins = None
    if odds_df is not None and not odds_df.empty:
        o = odds_df.copy()
        o['game_date'] = pd.to_datetime(o['game_date'])
        o['season_key'] = o['game_date'].map(
            lambda d: f"{d.year}-{str(d.year + 1)[-2:]}" if d.month >= 10
            else f"{d.year - 1}-{str(d.year)[-2:]}")
        total_bins = _qbins(o.loc[o['season_key'] == season, 'total'])

    return {
        'season': season,
        'total_bins': total_bins,
        'pace_bins': pace_bins,
        'team_game_poss': {k: float(v) for k, v in team_poss.items()},
        'team_def_tier': {k: str(v) for k, v in def_tier.items()},
    }


def build_context(df: pd.DataFrame,
                  odds_df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = df.copy()
    if 'pra' not in df.columns:
        df['pra'] = df['pts'] + df['reb'] + df['ast']
    # Pre-backfill store: HistoricalGameLog.stats payload may not yet carry
    # team_score/opp_score (backfill runs post-merge). Treat as unknown
    # rather than raising -- rows fall out via the existing NaN-bucket
    # conventions below (game_script, opp_def_tier).
    for score_col in ('team_score', 'opp_score'):
        if score_col not in df.columns:
            df[score_col] = float('nan')

    # --- simple row-wise dims
    df['ctx_home_away'] = df['home_away']
    df['ctx_role'] = df['starter'].map({True: 'starter', False: 'bench'})
    df['game_date'] = pd.to_datetime(df['game_date'])
    month = df['game_date'].dt.month
    df['ctx_season_segment'] = pd.cut(
        month.where(month >= 9, month + 12),   # Oct..Apr -> 10..16
        bins=SEGMENT_BINS, labels=list(SEGMENT_LABELS)).astype(object)

    # --- game_script from realized margin
    margin = (df['team_score'] - df['opp_score']).abs()
    df['ctx_game_script'] = pd.cut(
        margin, bins=[-1, 5, 14, 10_000],
        labels=['close', 'normal', 'blowout']).astype(object)
    df.loc[df['team_score'].isna() | df['opp_score'].isna(),
           'ctx_game_script'] = float('nan')

    # --- rest per player (game_date is already datetime64 above)
    df = df.sort_values(['player_id', 'game_date'])
    gap = df.groupby('player_id')['game_date'].diff().dt.days
    rest = gap - 1
    df['ctx_rest_bucket'] = pd.cut(
        rest.fillna(99), bins=REST_BINS,
        labels=list(REST_LABELS)).astype(object)

    # --- team-game table for pace + def tiers
    tg = _team_games(df)
    # pace: both teams' possession estimates summed per game
    tg['poss'] = tg['fga'] + 0.44 * tg['fta'] + tg['tov']
    game_poss = tg.groupby('game_id', as_index=False).agg(
        season=('season', 'first'), poss=('poss', 'sum'))
    game_poss['ctx_pace_tier'] = game_poss.groupby('season')['poss'].transform(
        lambda s: _safe_qcut(s, ('slow', 'mid', 'fast')))
    df = df.merge(game_poss[['game_id', 'ctx_pace_tier']],
                  on='game_id', how='left')

    # --- opp_def_tier: opponent's PRIOR season-to-date points allowed,
    # ranked cross-sectionally among all teams' priors as of that date.
    allowed = tg[['game_id', 'game_date', 'season', 'team_abbr',
                  'opp_score']].rename(columns={'opp_score': 'allowed'})
    allowed = allowed.sort_values(['season', 'team_abbr', 'game_date'])
    allowed['prior_allowed'] = allowed.groupby(
        ['season', 'team_abbr'])['allowed'].transform(
        lambda s: s.expanding().mean().shift(1))
    # rank each team's prior among teams with data in the same season+date.
    # Note: groupby(...).apply(func, include_groups=False) has a pandas
    # quirk where a *single* group can come back as a transposed DataFrame
    # instead of a concatenated Series (e.g. a season's opening night with
    # only one game). groupby(...)[col].transform(func) always aligns to
    # the original index regardless of group count, so use that instead.
    def _tier(s: pd.Series) -> pd.Series:
        pct = s.rank(pct=True)
        return pd.cut(pct, bins=[0, 1 / 3, 2 / 3, 1.0001],
                      labels=['top10', 'mid', 'bottom10']).astype(object)
    allowed['def_tier'] = allowed.groupby(
        ['season', 'game_date'])['prior_allowed'].transform(_tier)
    allowed.loc[allowed['prior_allowed'].isna(), 'def_tier'] = float('nan')
    df = df.merge(
        allowed[['game_id', 'team_abbr', 'def_tier']].rename(
            columns={'team_abbr': 'opp_abbr',
                     'def_tier': 'ctx_opp_def_tier'}),
        on=['game_id', 'opp_abbr'], how='left')

    # --- teammate_context: top-2 teammates by minutes-weighted usage
    df['_wusage'] = df.get('usage_pct', 0.0) * df['minutes']
    top2 = (df.groupby(['season', 'team_abbr', 'player_id'])['_wusage']
              .sum().reset_index()
              .sort_values(['season', 'team_abbr', '_wusage'],
                           ascending=[True, True, False]))
    top2['rank'] = top2.groupby(['season', 'team_abbr']).cumcount()
    key_players = top2[top2['rank'] < 3]        # top-3 pool; teammates = top-2 excl self
    key_by_team = key_players.groupby(['season', 'team_abbr'])[
        'player_id'].apply(list).to_dict()
    present = df.groupby(['game_id', 'team_abbr'])['player_id'].apply(set)
    def _teammate_bucket(row):
        keys = key_by_team.get((row['season'], row['team_abbr']), [])
        mates = [p for p in keys if p != row['player_id']][:2]
        if not mates:
            return float('nan')
        there = present.get((row['game_id'], row['team_abbr']), set())
        return 'full' if all(m in there for m in mates) else 'shorthanded'
    df['ctx_teammate_context'] = df.apply(_teammate_bucket, axis=1)
    df = df.drop(columns=['_wusage'])

    # --- odds dims
    if odds_df is None or odds_df.empty:
        df['ctx_fav_dog'] = float('nan')
        df['ctx_total_bucket'] = float('nan')
        return df
    o = odds_df.copy()
    o['game_date'] = pd.to_datetime(o['game_date'])
    o['season_key'] = o['game_date'].map(
        lambda d: f"{d.year}-{str(d.year + 1)[-2:]}" if d.month >= 10
        else f"{d.year - 1}-{str(d.year)[-2:]}")
    o['ctx_total_bucket'] = o.groupby('season_key')['total'].transform(
        lambda s: _safe_qcut(s, ('low', 'mid', 'high')))
    # join twice: once as home team, once as away
    merged_home = None
    merged_away = None
    for side, _other in (('home_abbr', 'away_abbr'), ('away_abbr', 'home_abbr')):
        sub = o[['game_date', side, 'spread', 'favored',
                 'ctx_total_bucket']].rename(columns={side: 'team_abbr'})
        sub['is_home_side'] = side == 'home_abbr'
        if side == 'home_abbr':
            merged_home = sub
        else:
            merged_away = sub
    odds_long = pd.concat([merged_home, merged_away], ignore_index=True)
    def _fav_bucket(row):
        return fav_dog_label(
            row['spread'],
            (row['favored'] == 'home') == row['is_home_side'])
    odds_long['ctx_fav_dog'] = odds_long.apply(_fav_bucket, axis=1)
    df = df.merge(
        odds_long[['game_date', 'team_abbr', 'ctx_fav_dog',
                   'ctx_total_bucket']],
        on=['game_date', 'team_abbr'], how='left')
    return df
