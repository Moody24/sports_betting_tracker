"""Scenario engine: shrunk conditional splits + agreement score (Plan B).

refresh_splits() is the nightly materialization: load store -> context ->
per (player, stat) singles + pairwise groupbys -> empirical-Bayes shrink ->
DELETE+INSERT ScenarioSplit. Derived data only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import combinations

import pandas as pd

from app import db
from app.models import HistoricalGameLog, JobLog, ScenarioSplit
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
        if not force:
            last = _last_success_utc()
            newest = db.session.query(
                db.func.max(HistoricalGameLog.fetched_at)).filter(
                HistoricalGameLog.sport == sport).scalar()
            if last is not None and newest is not None and \
                    _naive(newest) <= _naive(last):
                skipped_reason = 'no_new_data'
                return {'players': 0, 'rows': 0,
                        'skipped_reason': skipped_reason}

        df = load_frame(sport=sport)
        if df.empty:
            skipped_reason = 'empty_store'
            return {'players': 0, 'rows': 0, 'skipped_reason': skipped_reason}
        odds_df = load_odds_frame()
        ctx = build_context(df, odds_df=odds_df)

        # gate: >= min_games in the trailing 2 seasons
        seasons = sorted(ctx['season'].unique())[-2:]
        scope_all = ctx[ctx['season'].isin(seasons)]
        counts = scope_all.groupby('player_id')['game_id'].nunique()
        eligible = set(counts[counts >= min_games].index)
        players = len(eligible)

        ks = {stat: fit_prior_strength(scope_all, stat)
              for stat in SPLIT_STATS}
        current = seasons[-1]
        computed_at = datetime.now(timezone.utc)
        parts: list[pd.DataFrame] = []
        for scope_name, frame in (('all', scope_all),
                                  (current,
                                   scope_all[scope_all['season'] == current])):
            frame = frame[frame['player_id'].isin(eligible)]
            if frame.empty:
                continue
            names = {pid: n for pid, n in
                     frame.groupby('player_id')['player_name'].first().items()}
            baselines = frame.groupby('player_id')[list(SPLIT_STATS)].mean()
            dims = list(DIMENSIONS)
            combos = ([(d, None) for d in dims]
                      + list(combinations(dims, 2)))
            for dim1, dim2 in combos:
                cols = ['player_id', f'ctx_{dim1}'] + (
                    [f'ctx_{dim2}'] if dim2 else [])
                sub = frame.dropna(subset=cols[1:])
                if sub.empty:
                    continue
                agg = sub.groupby(cols, observed=True)[
                    list(SPLIT_STATS)].agg(['mean', 'count'])
                idx = agg.index
                pid_vals = idx.get_level_values(0).to_numpy()
                b1_vals = idx.get_level_values(1).to_numpy()
                b2_vals = idx.get_level_values(2).to_numpy() if dim2 else None
                for stat in SPLIT_STATS:
                    counts = agg[(stat, 'count')].to_numpy()
                    mask = counts >= MIN_N
                    if not mask.any():
                        continue
                    n_arr = counts[mask]
                    raw_arr = agg[(stat, 'mean')].to_numpy()[mask]
                    pid_stat = pid_vals[mask]
                    b1_stat = b1_vals[mask]
                    b2_stat = b2_vals[mask] if b2_vals is not None else None

                    base_arr = pd.Series(pid_stat).map(
                        baselines[stat]).to_numpy()
                    k = ks[stat]
                    # mirrors shrink() above, vectorized over the group array
                    shrunk_arr = (n_arr * raw_arr + k * base_arr) / (
                        n_arr + k)

                    part = pd.DataFrame({
                        'player_id': pid_stat,
                        'bucket1': pd.Series(b1_stat).astype(str),
                        'n': n_arr.astype(int),
                        'raw_mean': raw_arr.astype(float),
                        'baseline_mean': base_arr.astype(float),
                        'shrunk_mean': shrunk_arr.astype(float),
                    })
                    part['bucket2'] = (
                        pd.Series(b2_stat).astype(str)
                        if b2_stat is not None else None)
                    part['stat'] = stat
                    part['dim1'] = dim1
                    part['dim2'] = dim2
                    part['season_scope'] = scope_name
                    part['player_name'] = part['player_id'].map(names)
                    parts.append(part)

        if parts:
            final = pd.concat(parts, ignore_index=True)
            # Guard against pandas silently upcasting an all-None object
            # column to float64 NaN during concat -- bucket2/dim2 must
            # stay Python None (-> SQL NULL) for single-dim splits.
            for col in ('dim2', 'bucket2'):
                final[col] = final[col].astype(object)
                final.loc[final[col].isna(), col] = None
            batch = [dict(
                sport=sport, player_id=pid, player_name=pname, stat=stat,
                dim1=dim1, bucket1=b1, dim2=dim2, bucket2=b2,
                season_scope=scope, n=int(n), raw_mean=float(raw),
                shrunk_mean=float(shr), baseline_mean=float(base),
                computed_at=computed_at)
                for pid, pname, stat, dim1, b1, dim2, b2, scope, n, raw,
                shr, base in zip(
                    final['player_id'].tolist(), final['player_name'].tolist(),
                    final['stat'].tolist(), final['dim1'].tolist(),
                    final['bucket1'].tolist(), final['dim2'].tolist(),
                    final['bucket2'].tolist(), final['season_scope'].tolist(),
                    final['n'].tolist(), final['raw_mean'].tolist(),
                    final['shrunk_mean'].tolist(),
                    final['baseline_mean'].tolist())]
        else:
            batch = []

        ScenarioSplit.query.filter_by(sport=sport).delete()
        CHUNK = 50_000
        for i in range(0, len(batch), CHUNK):
            db.session.bulk_insert_mappings(
                ScenarioSplit, batch[i:i + CHUNK])
        import json as _json
        from app.models import ScenarioContextPack
        pack_payload = build_context_pack(df, odds_df)
        existing_pack = ScenarioContextPack.query.filter_by(sport=sport).first()
        if existing_pack is not None:
            db.session.delete(existing_pack)
        db.session.flush()
        db.session.add(ScenarioContextPack(
            sport=sport, payload=_json.dumps(pack_payload),
            computed_at=computed_at))
        db.session.commit()
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
        job.finished_at = datetime.now(timezone.utc)
        job.status = 'failed' if failed else 'success'
        job.message = (f"players={players} rows={rows_written}"
                       + (f" skipped={skipped_reason}" if skipped_reason
                          else ""))
        db.session.commit()


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
