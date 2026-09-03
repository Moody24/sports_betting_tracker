"""Scenario engine: shrunk conditional splits + agreement score (Plan B).

refresh_splits() is the nightly materialization: load store -> context ->
per (player, stat) singles + pairwise groupbys -> empirical-Bayes shrink ->
DELETE+INSERT ScenarioSplit. Derived data only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from itertools import combinations

import pandas as pd

from app import db
from app.models import (
    HistoricalGameLog, JobLog, ScenarioContextPack, ScenarioSplit,
)
from app.services.scenario_dimensions import (
    DIMENSIONS, SPLIT_STATS, build_context, build_context_pack, load_frame,
    load_odds_frame,
)

logger = logging.getLogger(__name__)

K_FLOOR, K_CAP = 2.0, 25.0
MIN_N = 3
MIN_GAMES_DEFAULT = 15


def shrink(raw: float, n: int, baseline: float, k: float) -> float:
    return (n * raw + k * baseline) / (n + k)


def fit_prior_strength(df, stat: str) -> float:
    """One-way random-effects ANOVA method-of-moments estimate of the
    empirical-Bayes prior strength k = within-player noise variance (MSW)
    divided by the estimated true between-player variance of means.

    Handles unbalanced group sizes (players with different game counts)
    via the standard Satterthwaite-style n0 correction. A noisy stat with
    similar players yields little/no real between-player signal -> the
    MoM between-variance estimate can go non-positive -> clamp to K_CAP
    (shrink hard toward baseline). Large, clearly-separated player means
    with low within-player noise -> small k (trust the raw split).
    """
    grouped = df.groupby('player_id')[stat]
    counts = grouped.count()
    means = grouped.mean()
    variances = grouped.var(ddof=1)

    n_groups = counts.shape[0]
    total_n = counts.sum()
    if n_groups < 2 or total_n <= n_groups:
        return K_CAP

    dof_i = (counts - 1).clip(lower=0)
    dof_total = dof_i.sum()
    ssw = (dof_i * variances.fillna(0.0)).sum()
    msw = ssw / dof_total if dof_total > 0 else 0.0

    grand_mean = (counts * means).sum() / total_n
    ssb = (counts * (means - grand_mean) ** 2).sum()
    msb = ssb / (n_groups - 1)

    n0 = (total_n - (counts ** 2).sum() / total_n) / (n_groups - 1)
    if not n0 or n0 != n0 or n0 <= 0:
        return K_CAP

    between_var = (msb - msw) / n0
    if not between_var or between_var != between_var or between_var <= 0:
        return K_CAP
    if not msw or msw != msw:
        return K_FLOOR

    k = msw / between_var
    return float(min(max(k, K_FLOOR), K_CAP))


def _naive(dt: datetime) -> datetime:
    """Strip tzinfo so DB-round-tripped (naive, stored-as-UTC) and
    freshly-constructed (tz-aware UTC) datetimes compare correctly."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _last_success_utc() -> datetime | None:
    job = (JobLog.query.filter_by(job_name='refresh-scenario-splits',
                                   status='success')
           .order_by(JobLog.finished_at.desc()).first())
    return job.finished_at if job else None


def _refresh_skip_reason(sport: str, force: bool) -> str | None:
    if force:
        return None
    last = _last_success_utc()
    newest = db.session.query(
        db.func.max(HistoricalGameLog.fetched_at),
    ).filter(HistoricalGameLog.sport == sport).scalar()
    if last is not None and newest is not None \
            and _naive(newest) <= _naive(last):
        return 'no_new_data'
    return None


def _eligible_split_scope(ctx: pd.DataFrame, min_games: int) -> tuple:
    """Return trailing-season data, current season, and eligible players."""
    seasons = sorted(ctx['season'].unique())[-2:]
    scope_all = ctx[ctx['season'].isin(seasons)]
    counts = scope_all.groupby('player_id')['game_id'].nunique()
    eligible = set(counts[counts >= min_games].index)
    return scope_all, seasons[-1], eligible


def _stat_split_part(agg: pd.DataFrame, baselines: pd.DataFrame,
                     names: dict, stat: str, dim1: str, dim2: str | None,
                     scope_name: str) -> pd.DataFrame | None:
    counts = agg[(stat, 'count')].to_numpy()
    mask = counts >= MIN_N
    if not mask.any():
        return None
    n_arr = counts[mask]
    raw_arr = agg[(stat, 'mean')].to_numpy()[mask]
    index = agg.index
    player_ids = index.get_level_values(0).to_numpy()[mask]
    bucket1 = index.get_level_values(1).to_numpy()[mask]
    bucket2 = (index.get_level_values(2).to_numpy()[mask]
               if dim2 else None)
    base_arr = pd.Series(player_ids).map(baselines[stat]).to_numpy()
    return pd.DataFrame({
        'player_id': player_ids,
        'player_name': pd.Series(player_ids).map(names),
        'stat': stat,
        'dim1': dim1,
        'bucket1': pd.Series(bucket1).astype(str),
        'dim2': dim2,
        'bucket2': (pd.Series(bucket2).astype(str)
                    if bucket2 is not None else None),
        'season_scope': scope_name,
        'n': n_arr.astype(int),
        'raw_mean': raw_arr.astype(float),
        'baseline_mean': base_arr.astype(float),
    })


def _scope_split_parts(frame: pd.DataFrame, scope_name: str, eligible: set,
                       prior_strengths: dict) -> list[pd.DataFrame]:
    frame = frame[frame['player_id'].isin(eligible)]
    if frame.empty:
        return []
    names = frame.groupby('player_id')['player_name'].first().to_dict()
    baselines = frame.groupby('player_id')[list(SPLIT_STATS)].mean()
    dimensions = list(DIMENSIONS)
    dimension_pairs = ([(dimension, None) for dimension in dimensions]
                       + list(combinations(dimensions, 2)))
    parts = []
    for dim1, dim2 in dimension_pairs:
        columns = ['player_id', f'ctx_{dim1}']
        columns.extend([f'ctx_{dim2}'] if dim2 else [])
        subset = frame.dropna(subset=columns[1:])
        if subset.empty:
            continue
        aggregate = subset.groupby(columns, observed=True)[
            list(SPLIT_STATS)].agg(['mean', 'count'])
        for stat in SPLIT_STATS:
            part = _stat_split_part(
                aggregate, baselines, names, stat, dim1, dim2, scope_name,
            )
            if part is None:
                continue
            k = prior_strengths[stat]
            part['shrunk_mean'] = (
                part['n'] * part['raw_mean']
                + k * part['baseline_mean']
            ) / (part['n'] + k)
            parts.append(part)
    return parts


def _split_batch(ctx: pd.DataFrame, sport: str, min_games: int,
                 computed_at: datetime) -> tuple[int, list[dict]]:
    scope_all, current, eligible = _eligible_split_scope(ctx, min_games)
    prior_strengths = {
        stat: fit_prior_strength(scope_all, stat) for stat in SPLIT_STATS
    }
    parts = []
    for scope_name, frame in (
        ('all', scope_all),
        (current, scope_all[scope_all['season'] == current]),
    ):
        parts.extend(_scope_split_parts(
            frame, scope_name, eligible, prior_strengths,
        ))
    if not parts:
        return len(eligible), []
    final = pd.concat(parts, ignore_index=True)
    for column in ('dim2', 'bucket2'):
        final[column] = final[column].astype(object)
        final.loc[final[column].isna(), column] = None
    records = final.to_dict('records')
    for record in records:
        record.update(sport=sport, computed_at=computed_at)
    return len(eligible), records


def _replace_materialization(sport: str, batch: list[dict],
                             pack_payload: dict,
                             computed_at: datetime) -> None:
    ScenarioSplit.query.filter_by(sport=sport).delete()
    chunk_size = 50_000
    for offset in range(0, len(batch), chunk_size):
        db.session.bulk_insert_mappings(
            ScenarioSplit, batch[offset:offset + chunk_size],
        )
    existing_pack = ScenarioContextPack.query.filter_by(sport=sport).first()
    if existing_pack is not None:
        db.session.delete(existing_pack)
    db.session.flush()
    db.session.add(ScenarioContextPack(
        sport=sport, payload=json.dumps(pack_payload),
        computed_at=computed_at,
    ))
    db.session.commit()


def _finish_refresh_job(job: JobLog, players: int, rows_written: int,
                        skipped_reason: str | None, failed: bool) -> None:
    job.finished_at = datetime.now(timezone.utc)
    job.status = 'failed' if failed else 'success'
    suffix = f" skipped={skipped_reason}" if skipped_reason else ""
    job.message = f"players={players} rows={rows_written}{suffix}"
    db.session.commit()


def refresh_splits(sport: str = 'nba', min_games: int = MIN_GAMES_DEFAULT,
                   force: bool = False) -> dict:
    job = JobLog(job_name='refresh-scenario-splits',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()
    players = rows_written = 0
    skipped_reason = None
    failed = False
    try:
        skipped_reason = _refresh_skip_reason(sport, force)
        if skipped_reason:
            return {'players': 0, 'rows': 0,
                    'skipped_reason': skipped_reason}
        frame = load_frame(sport=sport)
        if frame.empty:
            skipped_reason = 'empty_store'
            return {'players': 0, 'rows': 0,
                    'skipped_reason': skipped_reason}
        odds_frame = load_odds_frame()
        context = build_context(frame, odds_df=odds_frame)
        computed_at = datetime.now(timezone.utc)
        players, batch = _split_batch(
            context, sport, min_games, computed_at,
        )
        _replace_materialization(
            sport, batch, build_context_pack(frame, odds_frame), computed_at,
        )
        from app.services.player_crosswalk import clear_cache
        clear_cache()
        rows_written = len(batch)
        return {'players': players, 'rows': rows_written,
                'skipped_reason': None}
    except Exception as exc:
        db.session.rollback()
        failed = True
        skipped_reason = f'error: {exc}'
        logger.error("refresh-scenario-splits failed: %s", exc)
        raise
    finally:
        _finish_refresh_job(
            job, players, rows_written, skipped_reason, failed,
        )


def load_agreement_splits(player_id: str, stat: str,
                          sport: str = 'nba') -> list:
    """All 'all'-scope splits for one (player, stat) — the candidate rows
    agreement_score matches against. Callers scoring many lines for the
    same player should load once and pass via ``splits=``."""
    return ScenarioSplit.query.filter_by(
        sport=sport, player_id=str(player_id), stat=stat,
        season_scope='all').all()


def agreement_score(player_id: str, stat: str, line: float,
                     context: dict, sport: str = 'nba',
                     splits: list | None = None) -> tuple[float, int]:
    """Signed weighted share of applicable splits vs the line (+ = over).

    ``splits`` (optional) is a prefetched load_agreement_splits() result;
    when provided (even empty) no query is issued."""
    if splits is None:
        splits = load_agreement_splits(player_id, stat, sport)
    matches = []
    for s in splits:
        if s.dim1 not in context or context[s.dim1] != s.bucket1:
            continue
        if s.dim2 is not None and (
                s.dim2 not in context or context[s.dim2] != s.bucket2):
            continue
        matches.append(s)
    if not matches:
        return 0.0, 0
    total_w = sum(s.n for s in matches)
    signed = sum(s.n * (1 if s.shrunk_mean > line else -1) for s in matches)
    return signed / total_w, len(matches)
