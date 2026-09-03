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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PostmortemEvidence:
    """All measured evidence consumed by diagnosis and reason rules."""

    ctx: dict
    actual_stat: float
    projected_stat: Optional[float]
    projection_error: Optional[float]
    player_variance: float
    expected_minutes: Optional[float]
    actual_minutes: Optional[float]
    minutes_delta: Optional[float]
    expected_attempts: Optional[float]
    actual_attempts: Optional[float]
    attempts_delta: Optional[float]
    overtime_flag: bool
    blowout_flag: bool
    line: float
    miss_margin: float


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
    evidence = PostmortemEvidence(
        ctx=ctx,
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
        line=line,
        miss_margin=miss_margin,
    )
    reasons = _assign_reasons(evidence)

    primary = reasons[0][0] if len(reasons) >= 1 else PostmortemReason.UNKNOWN.value
    secondary = reasons[1][0] if len(reasons) >= 2 else None
    tertiary = reasons[2][0] if len(reasons) >= 3 else None
    confidence = reasons[0][1] if reasons else 0.5

    # ── Full diagnosis payload ──────────────────────────────────────
    diagnosis = _build_diagnosis(evidence, reasons)

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

ScoredReason = tuple[str, float]


def _reason(code: PostmortemReason, score: float) -> ScoredReason:
    return code.value, round(min(score, 0.95), 3)


def _game_context_reasons(evidence: PostmortemEvidence) -> list[ScoredReason]:
    reasons = []
    if evidence.overtime_flag:
        reasons.append(_reason(PostmortemReason.OT_VARIANCE, 0.82))
    if evidence.blowout_flag:
        reasons.append(_reason(PostmortemReason.BLOWOUT_DISTORTION, 0.78))
    return reasons


def _minutes_reasons(evidence: PostmortemEvidence) -> list[ScoredReason]:
    if evidence.minutes_delta is None:
        return []
    absolute_delta = abs(evidence.minutes_delta)
    if absolute_delta < 4:
        return []
    if absolute_delta < 8:
        return [_reason(PostmortemReason.MINUTES_MISS, 0.55)]
    reasons = [_reason(
        PostmortemReason.MINUTES_MISS,
        min(0.90, 0.62 + absolute_delta / 35.0),
    )]
    stable_role = evidence.ctx.get('minutes_trend', 'stable') == 'stable'
    if stable_role and absolute_delta >= 10:
        reasons.append(_reason(PostmortemReason.ROLE_CHANGE, 0.72))
    return reasons


def _volume_reasons(evidence: PostmortemEvidence) -> list[ScoredReason]:
    if evidence.attempts_delta is None or not evidence.expected_attempts:
        return []
    if evidence.expected_attempts <= 1.0:
        return []
    swing = evidence.attempts_delta / evidence.expected_attempts
    score = min(0.88, 0.60 + abs(swing) * 0.55)
    if swing >= 0.35:
        return [_reason(PostmortemReason.VOLUME_SPIKE, score)]
    if swing <= -0.35:
        return [_reason(PostmortemReason.VOLUME_DROP, score)]
    return []


def _efficiency_reasons(
    evidence: PostmortemEvidence,
    volume_reasons: list[ScoredReason],
) -> list[ScoredReason]:
    if volume_reasons or not evidence.actual_attempts or not evidence.expected_attempts:
        return []
    if evidence.projected_stat is None:
        return []
    expected_rate = evidence.projected_stat / evidence.expected_attempts
    actual_rate = evidence.actual_stat / evidence.actual_attempts
    delta = actual_rate - expected_rate
    if delta > 0.15:
        return [_reason(PostmortemReason.EFFICIENCY_SPIKE, 0.68)]
    if delta < -0.15:
        return [_reason(PostmortemReason.EFFICIENCY_DROP, 0.68)]
    return []


def _edge_reasons(evidence: PostmortemEvidence) -> list[ScoredReason]:
    projected_edge = float(evidence.ctx.get('projected_edge', 0) or 0)
    if projected_edge < 0:
        return [_reason(PostmortemReason.INSUFFICIENT_EDGE, 0.65)]
    if abs(projected_edge) < 0.05:
        return [_reason(PostmortemReason.LINE_VALUE_MISS, 0.58)]
    return []


def _model_miss_reasons(
    evidence: PostmortemEvidence,
    accumulated: list[ScoredReason],
) -> list[ScoredReason]:
    if evidence.projection_error is None or evidence.player_variance <= 0:
        return []
    structural_codes = {
        PostmortemReason.VOLUME_SPIKE.value,
        PostmortemReason.VOLUME_DROP.value,
        PostmortemReason.MINUTES_MISS.value,
        PostmortemReason.OT_VARIANCE.value,
        PostmortemReason.BLOWOUT_DISTORTION.value,
    }
    has_structural_driver = any(code in structural_codes for code, _ in accumulated)
    z_error = abs(evidence.projection_error) / evidence.player_variance
    if z_error <= 2.0 or has_structural_driver:
        return []
    return [_reason(
        PostmortemReason.PROJECTION_MODEL_MISS,
        min(0.80, 0.50 + z_error * 0.06),
    )]


def _variance_reasons(evidence: PostmortemEvidence) -> list[ScoredReason]:
    reasons = []
    error = evidence.projection_error
    variance = evidence.player_variance
    if error is not None and variance >= 4.0 and abs(error) > variance:
        reasons.append(_reason(PostmortemReason.HIGH_VARIANCE_EVENT, 0.62))
    if error is not None and variance > 0:
        z_error = abs(error) / variance
        if z_error <= 1.0 and abs(evidence.miss_margin) <= 1.5:
            reasons.append(_reason(PostmortemReason.NORMAL_VARIANCE, 0.75))
    elif error is None and abs(evidence.miss_margin) <= 1.0:
        reasons.append(_reason(PostmortemReason.NORMAL_VARIANCE, 0.55))
    return reasons

def _assign_reasons(evidence: PostmortemEvidence) -> list[tuple[str, float]]:
    """Return a deduplicated, confidence-sorted list of (reason_code, confidence).

    Uses deterministic business rules.  Scores are 0–1; higher = more confident.
    At most the top 3 reasons are used by the caller.
    """
    scored = _game_context_reasons(evidence)
    scored.extend(_minutes_reasons(evidence))
    volume_reasons = _volume_reasons(evidence)
    scored.extend(volume_reasons)
    scored.extend(_efficiency_reasons(evidence, volume_reasons))
    scored.extend(_edge_reasons(evidence))
    scored.extend(_model_miss_reasons(evidence, scored))
    scored.extend(_variance_reasons(evidence))
    if not scored:
        scored.append(_reason(PostmortemReason.UNKNOWN, 0.40))

    best: dict[str, float] = {}
    for code, score in scored:
        if score > best.get(code, -1):
            best[code] = score
    return sorted(best.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Diagnosis JSON builder
# ---------------------------------------------------------------------------

def _build_diagnosis(
    evidence: PostmortemEvidence,
    reasons: list[tuple[str, float]],
) -> dict:
    """Assemble the full diagnostic payload stored as JSON."""
    ctx = evidence.ctx
    return {
        # Core stat comparison
        'projected_stat': evidence.projected_stat,
        'actual_stat': evidence.actual_stat,
        'prop_line': evidence.line,
        'miss_margin': evidence.miss_margin,
        'projection_error': (
            round(evidence.projection_error, 2)
            if evidence.projection_error is not None else None
        ),
        # Minutes
        'expected_minutes': (
            round(evidence.expected_minutes, 1)
            if evidence.expected_minutes is not None else None
        ),
        'actual_minutes': (
            round(evidence.actual_minutes, 1)
            if evidence.actual_minutes is not None else None
        ),
        'minutes_delta': (
            round(evidence.minutes_delta, 1)
            if evidence.minutes_delta is not None else None
        ),
        # Attempts / volume
        'expected_attempts': (
            round(evidence.expected_attempts, 1)
            if evidence.expected_attempts is not None else None
        ),
        'actual_attempts': evidence.actual_attempts,
        'attempts_delta': (
            round(evidence.attempts_delta, 1)
            if evidence.attempts_delta is not None else None
        ),
        # Model uncertainty
        'player_variance': evidence.player_variance,
        'projected_edge': float(ctx.get('projected_edge', 0) or 0),
        'confidence_tier': ctx.get('confidence_tier'),
        # Contextual flags at bet placement time
        'pregame_minutes_trend': ctx.get('minutes_trend'),
        'pregame_player_trend': ctx.get('player_last5_trend'),
        'pregame_back_to_back': ctx.get('back_to_back'),
        'pregame_injury_returning': ctx.get('injury_returning'),
        # Game-context
        'overtime_flag': evidence.overtime_flag,
        'blowout_flag': evidence.blowout_flag,
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
