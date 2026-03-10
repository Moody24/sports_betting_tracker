"""Bet postmortem analysis service.

After a player-prop leg settles, this service:
  1. Loads pregame expectations from PickContext
  2. Loads actual game stats from PlayerGameLog
  3. Computes diagnostic deltas (minutes, attempts, efficiency, etc.)
  4. Assigns structured reason codes via a deterministic rules engine
  5. Saves a BetPostmortem record (idempotent — safe to re-run)

Usage::

    from app.services.postmortem_service import create_or_update_postmortem
    postmortem = create_or_update_postmortem(bet)

Only player-prop bets with a known actual result are analysed.
Non-prop bets and pushes are skipped gracefully (returns None).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, date as date_type
from typing import Optional

from app import db
from app.enums import BetType, Outcome, PostmortemReason
from app.models import Bet, BetPostmortem, GameSnapshot, PlayerGameLog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prop → attempts stat key on PlayerGameLog
# Only props where shot/usage attempts are a meaningful signal.
# ---------------------------------------------------------------------------
PROP_TO_ATTEMPTS_KEY: dict[str, str] = {
    'player_points': 'fga',
    'player_threes': 'fg3a',
}

# When total score exceeds this, we flag OT (NBA avg ~215 pts; OT adds ~25).
_OT_TOTAL_SCORE_THRESHOLD = 230
# Blowout when score differential exceeds this.
_BLOWOUT_DIFF_THRESHOLD = 22


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_or_update_postmortem(bet: Bet) -> Optional[BetPostmortem]:
    """Analyse a settled prop leg and upsert a BetPostmortem record.

    Safe to call multiple times — existing records are updated in place so
    re-running settlement never creates duplicate postmortems.

    Returns the saved BetPostmortem, or None if the bet is not eligible
    (e.g. not a player prop, no actual result, or a push/DNP).
    """
    if not bet.is_player_prop:
        return None
    if bet.actual_total is None:
        return None
    # Push = DNP void; no useful analysis possible.
    if bet.outcome == Outcome.PUSH.value:
        return None
    if bet.outcome == Outcome.PENDING.value:
        return None

    match_date = (
        bet.match_date.date()
        if isinstance(bet.match_date, datetime)
        else bet.match_date
    )

    # ── Pregame context ─────────────────────────────────────────────
    pick_ctx = bet.pick_context
    ctx: dict = {}
    projected_stat: Optional[float] = None
    if pick_ctx:
        ctx = pick_ctx.context
        projected_stat = pick_ctx.projected_stat

    # ── Actual game stats from PlayerGameLog ────────────────────────
    actual_log = _get_game_log_for_date(bet.player_name, match_date)
    history_logs = _get_history_before(bet.player_name, match_date, n=10)

    # ── Compute expected baselines from history ─────────────────────
    expected_minutes = _avg_attr(history_logs, 'minutes')
    actual_minutes = _attr(actual_log, 'minutes')
    minutes_delta = _delta(actual_minutes, expected_minutes)

    attempts_key = PROP_TO_ATTEMPTS_KEY.get(bet.prop_type or '')
    expected_attempts = _avg_attr(history_logs, attempts_key) if attempts_key else None
    actual_attempts = _attr(actual_log, attempts_key) if attempts_key else None
    attempts_delta = _delta(actual_attempts, expected_attempts)

    # ── Stat values ──────────────────────────────────────────────────
    actual_stat = float(bet.actual_total)
    projection_error = (
        round(actual_stat - projected_stat, 3) if projected_stat is not None else None
    )
    player_variance = float(ctx.get('player_variance', 0) or 0)

    # ── Miss margin: signed distance from line (positive = correct side) ──
    line = float(bet.prop_line)
    if bet.bet_type == BetType.OVER.value:
        miss_margin = round(actual_stat - line, 2)
    else:
        miss_margin = round(line - actual_stat, 2)

    # ── Game-context flags from GameSnapshot ────────────────────────
    overtime_flag, blowout_flag = _game_context_flags(
        bet.external_game_id, match_date
    )

    # ── Reason assignment ────────────────────────────────────────────
    reasons = _assign_reasons(
        ctx=ctx,
        bet_type=bet.bet_type,
        actual_stat=actual_stat,
        projected_stat=projected_stat,
        projection_error=projection_error,
        player_variance=player_variance,
        actual_minutes=actual_minutes,
        expected_minutes=expected_minutes,
        minutes_delta=minutes_delta,
        actual_attempts=actual_attempts,
        expected_attempts=expected_attempts,
        attempts_delta=attempts_delta,
        overtime_flag=overtime_flag,
        blowout_flag=blowout_flag,
        miss_margin=miss_margin,
    )

    primary = reasons[0][0] if len(reasons) >= 1 else PostmortemReason.UNKNOWN.value
    secondary = reasons[1][0] if len(reasons) >= 2 else None
    tertiary = reasons[2][0] if len(reasons) >= 3 else None
    confidence = reasons[0][1] if reasons else 0.5

    # ── Full diagnosis payload ──────────────────────────────────────
    diagnosis = _build_diagnosis(
        ctx=ctx,
        actual_stat=actual_stat,
        projected_stat=projected_stat,
        projection_error=projection_error,
        line=line,
        miss_margin=miss_margin,
        expected_minutes=expected_minutes,
        actual_minutes=actual_minutes,
        minutes_delta=minutes_delta,
        expected_attempts=expected_attempts,
        actual_attempts=actual_attempts,
        attempts_delta=attempts_delta,
        player_variance=player_variance,
        overtime_flag=overtime_flag,
        blowout_flag=blowout_flag,
        reasons=reasons,
    )

    # ── Upsert ───────────────────────────────────────────────────────
    pm = BetPostmortem.query.filter_by(bet_id=bet.id).first()
    if pm is None:
        pm = BetPostmortem(bet_id=bet.id, created_at=datetime.now(timezone.utc))
        db.session.add(pm)

    pm.player_name = bet.player_name
    pm.game_date = match_date
    pm.stat_type = bet.prop_type
    pm.bet_side = bet.bet_type
    pm.prop_line = line
    pm.projected_stat = projected_stat
    pm.actual_stat = actual_stat
    pm.projection_error = projection_error
    pm.miss_margin = miss_margin
    pm.expected_minutes = round(expected_minutes, 1) if expected_minutes else None
    pm.actual_minutes = round(actual_minutes, 1) if actual_minutes is not None else None
    pm.minutes_delta = round(minutes_delta, 1) if minutes_delta is not None else None
    pm.expected_attempts = (
        round(expected_attempts, 1) if expected_attempts is not None else None
    )
    pm.actual_attempts = actual_attempts
    pm.attempts_delta = (
        round(attempts_delta, 1) if attempts_delta is not None else None
    )
    pm.expected_pace = float(ctx.get('opp_pace', 0) or 0) or None
    pm.overtime_flag = overtime_flag
    pm.blowout_flag = blowout_flag
    pm.primary_reason_code = primary
    pm.secondary_reason_code = secondary
    pm.tertiary_reason_code = tertiary
    pm.reason_confidence = round(confidence, 3)
    pm.diagnosis_json = json.dumps(diagnosis)
    pm.updated_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to save postmortem for bet_id=%s", bet.id)
        return None

    logger.debug(
        "Postmortem saved: bet_id=%s reason=%s conf=%.2f",
        bet.id,
        primary,
        confidence,
    )
    return pm


# ---------------------------------------------------------------------------
# Reason-code assignment engine
# ---------------------------------------------------------------------------

def _assign_reasons(
    *,
    ctx: dict,
    bet_type: str,
    actual_stat: float,
    projected_stat: Optional[float],
    projection_error: Optional[float],
    player_variance: float,
    actual_minutes: Optional[float],
    expected_minutes: Optional[float],
    minutes_delta: Optional[float],
    actual_attempts: Optional[float],
    expected_attempts: Optional[float],
    attempts_delta: Optional[float],
    overtime_flag: bool,
    blowout_flag: bool,
    miss_margin: float,
) -> list[tuple[str, float]]:
    """Return a deduplicated, confidence-sorted list of (reason_code, confidence).

    Uses deterministic business rules.  Scores are 0–1; higher = more confident.
    At most the top 3 reasons are used by the caller.
    """
    scored: list[tuple[str, float]] = []

    def _add(code: PostmortemReason, score: float) -> None:
        scored.append((code.value, round(min(score, 0.95), 3)))

    # ── 1. Game-context flags (high confidence when present) ─────────
    if overtime_flag:
        _add(PostmortemReason.OT_VARIANCE, 0.82)
    if blowout_flag:
        _add(PostmortemReason.BLOWOUT_DISTORTION, 0.78)

    # ── 2. Minutes variance ──────────────────────────────────────────
    if minutes_delta is not None:
        abs_min = abs(minutes_delta)
        if abs_min >= 8:
            # Large change — very likely a primary driver
            score = min(0.90, 0.62 + abs_min / 35.0)
            _add(PostmortemReason.MINUTES_MISS, score)
            # If minutes trend was stable pre-game, this is an unexpected role shift
            if ctx.get('minutes_trend', 'stable') == 'stable' and abs_min >= 10:
                _add(PostmortemReason.ROLE_CHANGE, 0.72)
        elif abs_min >= 4:
            # Moderate change — worth flagging as secondary
            _add(PostmortemReason.MINUTES_MISS, 0.55)

    # ── 3. Volume (attempt) variance ─────────────────────────────────
    if (
        attempts_delta is not None
        and expected_attempts is not None
        and expected_attempts > 1.0
    ):
        pct_swing = attempts_delta / expected_attempts
        if pct_swing >= 0.35:
            score = min(0.88, 0.60 + abs(pct_swing) * 0.55)
            _add(PostmortemReason.VOLUME_SPIKE, score)
        elif pct_swing <= -0.35:
            score = min(0.88, 0.60 + abs(pct_swing) * 0.55)
            _add(PostmortemReason.VOLUME_DROP, score)

    # ── 4. Efficiency variance ────────────────────────────────────────
    # Only meaningful when attempts data is available and volume was NOT the driver
    volume_was_driver = any(
        c in (PostmortemReason.VOLUME_SPIKE.value, PostmortemReason.VOLUME_DROP.value)
        for c, _ in scored
    )
    if (
        not volume_was_driver
        and actual_attempts is not None
        and actual_attempts > 0
        and expected_attempts is not None
        and expected_attempts > 0
        and projected_stat is not None
    ):
        expected_rate = projected_stat / expected_attempts
        actual_rate = actual_stat / actual_attempts
        eff_delta = actual_rate - expected_rate
        if eff_delta > 0.15:
            _add(PostmortemReason.EFFICIENCY_SPIKE, 0.68)
        elif eff_delta < -0.15:
            _add(PostmortemReason.EFFICIENCY_DROP, 0.68)

    # ── 5. Line / edge quality ────────────────────────────────────────
    projected_edge = float(ctx.get('projected_edge', 0) or 0)
    if projected_edge < 0:
        _add(PostmortemReason.INSUFFICIENT_EDGE, 0.65)
    elif abs(projected_edge) < 0.05:
        _add(PostmortemReason.LINE_VALUE_MISS, 0.58)

    # ── 6. Projection model miss ──────────────────────────────────────
    # Large residual that cannot be explained by volume or minutes changes
    if projection_error is not None and player_variance and player_variance > 0:
        z_error = abs(projection_error) / player_variance
        structural_driver = any(
            c in (
                PostmortemReason.VOLUME_SPIKE.value,
                PostmortemReason.VOLUME_DROP.value,
                PostmortemReason.MINUTES_MISS.value,
                PostmortemReason.OT_VARIANCE.value,
                PostmortemReason.BLOWOUT_DISTORTION.value,
            )
            for c, _ in scored
        )
        if z_error > 2.0 and not structural_driver:
            score = min(0.80, 0.50 + z_error * 0.06)
            _add(PostmortemReason.PROJECTION_MODEL_MISS, score)

    # ── 7. High-variance event (big miss for a volatile player) ───────
    if (
        projection_error is not None
        and player_variance >= 4.0
        and abs(projection_error) > player_variance
    ):
        _add(PostmortemReason.HIGH_VARIANCE_EVENT, 0.62)

    # ── 8. Normal variance (everything within expected band) ──────────
    if projection_error is not None and player_variance and player_variance > 0:
        z_error = abs(projection_error) / player_variance
        if z_error <= 1.0 and abs(miss_margin) <= 1.5:
            _add(PostmortemReason.NORMAL_VARIANCE, 0.75)
    elif projection_error is None and abs(miss_margin) <= 1.0:
        # No projection context but loss was narrow — assume normal variance
        _add(PostmortemReason.NORMAL_VARIANCE, 0.55)

    # ── Fallback ──────────────────────────────────────────────────────
    if not scored:
        _add(PostmortemReason.UNKNOWN, 0.40)

    # Deduplicate keeping highest score per code, then sort desc by score
    best: dict[str, float] = {}
    for code, score in scored:
        if score > best.get(code, -1):
            best[code] = score
    return sorted(best.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Diagnosis JSON builder
# ---------------------------------------------------------------------------

def _build_diagnosis(
    *,
    ctx: dict,
    actual_stat: float,
    projected_stat: Optional[float],
    projection_error: Optional[float],
    line: float,
    miss_margin: float,
    expected_minutes: Optional[float],
    actual_minutes: Optional[float],
    minutes_delta: Optional[float],
    expected_attempts: Optional[float],
    actual_attempts: Optional[float],
    attempts_delta: Optional[float],
    player_variance: float,
    overtime_flag: bool,
    blowout_flag: bool,
    reasons: list[tuple[str, float]],
) -> dict:
    """Assemble the full diagnostic payload stored as JSON."""
    return {
        # Core stat comparison
        'projected_stat': projected_stat,
        'actual_stat': actual_stat,
        'prop_line': line,
        'miss_margin': miss_margin,
        'projection_error': (
            round(projection_error, 2) if projection_error is not None else None
        ),
        # Minutes
        'expected_minutes': (
            round(expected_minutes, 1) if expected_minutes is not None else None
        ),
        'actual_minutes': (
            round(actual_minutes, 1) if actual_minutes is not None else None
        ),
        'minutes_delta': (
            round(minutes_delta, 1) if minutes_delta is not None else None
        ),
        # Attempts / volume
        'expected_attempts': (
            round(expected_attempts, 1) if expected_attempts is not None else None
        ),
        'actual_attempts': actual_attempts,
        'attempts_delta': (
            round(attempts_delta, 1) if attempts_delta is not None else None
        ),
        # Model uncertainty
        'player_variance': player_variance,
        'projected_edge': float(ctx.get('projected_edge', 0) or 0),
        'confidence_tier': ctx.get('confidence_tier'),
        # Contextual flags at bet placement time
        'pregame_minutes_trend': ctx.get('minutes_trend'),
        'pregame_player_trend': ctx.get('player_last5_trend'),
        'pregame_back_to_back': ctx.get('back_to_back'),
        'pregame_injury_returning': ctx.get('injury_returning'),
        # Game-context
        'overtime_flag': overtime_flag,
        'blowout_flag': blowout_flag,
        # Scored reasons with confidence
        'reason_scores': [(code, round(score, 3)) for code, score in reasons[:5]],
    }


# ---------------------------------------------------------------------------
# Data-access helpers
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    """Normalise a player name for fuzzy matching (strip punctuation, lowercase)."""
    return re.sub(r"[.\'\-]", "", name or "").lower().strip()


def _get_game_log_for_date(
    player_name: str, game_date: date_type
) -> Optional[PlayerGameLog]:
    """Return the PlayerGameLog row for the player on game_date (exact match)."""
    name_norm = _norm_name(player_name)
    rows = (
        PlayerGameLog.query
        .filter(PlayerGameLog.game_date == game_date)
        .all()
    )
    for row in rows:
        if _norm_name(row.player_name) == name_norm:
            return row
    # Partial match fallback
    for row in rows:
        if name_norm in _norm_name(row.player_name) or _norm_name(row.player_name) in name_norm:
            return row
    return None


def _get_history_before(
    player_name: str, before_date: date_type, n: int = 10
) -> list[PlayerGameLog]:
    """Return the player's last N game logs strictly before before_date."""
    name_norm = _norm_name(player_name)
    rows = (
        PlayerGameLog.query
        .filter(PlayerGameLog.game_date < before_date)
        .order_by(PlayerGameLog.game_date.desc())
        .limit(n * 3)  # fetch extra; filter by name below
        .all()
    )
    matched = [
        r for r in rows
        if _norm_name(r.player_name) == name_norm
        or name_norm in _norm_name(r.player_name)
        or _norm_name(r.player_name) in name_norm
    ]
    return matched[:n]


def _get_game_snapshot(
    espn_id: Optional[str], game_date: date_type
) -> Optional[GameSnapshot]:
    """Return the GameSnapshot for this game, used for OT/blowout detection."""
    if not espn_id:
        return None
    return GameSnapshot.query.filter_by(espn_id=espn_id).first()


def _game_context_flags(
    espn_id: Optional[str], game_date: date_type
) -> tuple[bool, bool]:
    """Return (overtime_flag, blowout_flag) from GameSnapshot scores."""
    snap = _get_game_snapshot(espn_id, game_date)
    if snap is None:
        return False, False

    home = snap.home_score or 0
    away = snap.away_score or 0
    total = home + away
    diff = abs(home - away)

    overtime = total > _OT_TOTAL_SCORE_THRESHOLD
    blowout = diff > _BLOWOUT_DIFF_THRESHOLD
    return overtime, blowout


def _avg_attr(logs: list, attr: Optional[str]) -> Optional[float]:
    """Average of a numeric attribute across a list of logs; None if no data."""
    if not attr or not logs:
        return None
    vals = [float(getattr(r, attr, 0) or 0) for r in logs]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _attr(log: Optional[PlayerGameLog], attr: Optional[str]) -> Optional[float]:
    """Safely extract a float attribute from a PlayerGameLog row."""
    if log is None or not attr:
        return None
    val = getattr(log, attr, None)
    return float(val) if val is not None else None


def _delta(actual: Optional[float], expected: Optional[float]) -> Optional[float]:
    """Return actual - expected, or None if either is unavailable."""
    if actual is None or expected is None:
        return None
    return round(actual - expected, 2)


# ---------------------------------------------------------------------------
# Batch helpers (used by backfill CLI and scheduler)
# ---------------------------------------------------------------------------

def backfill_postmortems(bets: list[Bet], *, skip_existing: bool = True) -> dict:
    """Create postmortems for a batch of already-settled bets.

    Args:
        bets: list of Bet objects (should already be settled).
        skip_existing: when True, bets that already have a postmortem are skipped.

    Returns a summary dict with created/skipped/error counts.
    """
    created = skipped = errors = ineligible = 0

    for bet in bets:
        try:
            if skip_existing and bet.postmortem is not None:
                skipped += 1
                continue

            result = create_or_update_postmortem(bet)
            if result is None:
                ineligible += 1
            else:
                created += 1
        except Exception:
            logger.exception("Backfill error for bet_id=%s", bet.id)
            errors += 1

    logger.info(
        "Postmortem backfill: created=%d skipped=%d ineligible=%d errors=%d",
        created,
        skipped,
        ineligible,
        errors,
    )
    return {
        'created': created,
        'skipped': skipped,
        'ineligible': ineligible,
        'errors': errors,
    }
