import logging
from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash
from flask_login import current_user, login_required
from sqlalchemy import func, case, text

from app import db
from app.enums import Outcome
from app.models import Bet, compute_bets_net_pl

logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)



@main.route('/ready')
def ready():
    """Readiness endpoint that verifies dependencies like the database."""
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='healthy', database='connected'), 200
    except Exception as exc:
        logger.error('Health check failed: %s', exc)
        return jsonify(status='unhealthy', database='disconnected'), 503


@main.route('/')
def home():
    return render_template('home.html')


@main.route('/dashboard')
@login_required
def dashboard():
    uid = current_user.id

    # ── Aggregate stats in a single SQL query ─────────────────────────
    agg = db.session.query(
        func.count(Bet.id).label('total'),
        func.coalesce(func.sum(case((Bet.outcome == Outcome.WIN.value, 1), else_=0)), 0).label('wins'),
        func.coalesce(func.sum(case((Bet.outcome == Outcome.LOSE.value, 1), else_=0)), 0).label('losses'),
        func.coalesce(func.sum(Bet.bet_amount), 0.0).label('wagered'),
    ).filter_by(user_id=uid).one()

    total_bets = int(agg.total)
    wins = int(agg.wins)
    losses = int(agg.losses)
    wagered = float(agg.wagered)

    # ── Recent bets (capped by SQL LIMIT) ─────────────────────────────
    recent_bets = (
        Bet.query.filter_by(user_id=uid)
        .order_by(Bet.created_at.desc())
        .limit(7)
        .all()
    )

    # Pre-compute parlay leg counts in one query to avoid N+1 in display_label
    _attach_parlay_leg_counts(recent_bets)

    # ── Graded bets: single query reused for net P/L, streak, and cumulative chart
    graded_bets = (
        Bet.query.filter_by(user_id=uid)
        .filter(Bet.outcome.in_([Outcome.WIN.value, Outcome.LOSE.value, Outcome.PUSH.value]))
        .order_by(Bet.match_date.desc())
        .limit(200)
        .all()
    )

    # ── Net P/L (reuse graded_bets instead of separate query) ─────────
    units_won = compute_bets_net_pl(graded_bets)
    roi = (units_won / wagered * 100) if wagered else 0
    graded_count = wins + losses
    win_pct = (wins / graded_count * 100) if graded_count else 0

    # ── Streak (walk graded_bets until streak breaks — no extra query) ─
    streak = 0
    streak_type = 'No streak'
    for b in graded_bets:
        if b.outcome == Outcome.PUSH.value:
            continue
        if streak == 0:
            streak = 1
            streak_type = b.outcome
        elif b.outcome == streak_type:
            streak += 1
        else:
            break
    current_streak = f"{streak} {streak_type.title()}" if streak else 'No streak'

    # ── Daily P/L chart (recent bets, parlay-aware grouping) ──────────
    daily_bets: dict = defaultdict(list)
    for b in recent_bets:
        daily_bets[b.match_date.strftime('%b %d')].append(b)
    chart_labels = list(reversed(list(daily_bets.keys())))
    chart_values = [round(compute_bets_net_pl(daily_bets[lbl]), 2) for lbl in chart_labels]

    # ── Cumulative P/L (reuse graded_bets, last 60 → collapse parlays) ─
    cumul_bets = list(reversed(graded_bets[:60]))  # oldest first
    parlay_seen: set = set()
    cumul_events: list = []
    for b in cumul_bets:
        if b.is_parlay and b.parlay_id:
            if b.parlay_id in parlay_seen:
                continue
            parlay_seen.add(b.parlay_id)
            legs = [x for x in cumul_bets if x.is_parlay and x.parlay_id == b.parlay_id]
            cumul_events.append((b.match_date.strftime('%b %d'), Bet.parlay_profit_loss(legs)))
        else:
            cumul_events.append((b.match_date.strftime('%b %d'), b.profit_loss()))

    cumul_events = cumul_events[-30:]
    cumulative = 0.0
    cumul_labels = []
    cumul_values = []
    for label, pl in cumul_events:
        cumulative = round(cumulative + pl, 2)
        cumul_labels.append(label)
        cumul_values.append(cumulative)

    stats = {
        'total_bets': total_bets,
        'wins': wins,
        'losses': losses,
        'wagered': wagered,
        'net': units_won,
        'units_won': units_won,
        'roi': round(roi, 1),
        'win_pct': round(win_pct, 1),
        'current_streak': current_streak,
    }

    # ── Today's top plays (cached to avoid blocking page loads) ───────
    top_plays, best_parlay = _get_cached_plays()

    return render_template(
        'dashboard.html',
        stats=stats,
        recent_bets=recent_bets,
        chart_labels=chart_labels,
        chart_values=chart_values,
        cumul_labels=cumul_labels,
        cumul_values=cumul_values,
        top_plays=top_plays,
        best_parlay=best_parlay,
    )


def _attach_parlay_leg_counts(bets: list) -> None:
    """Pre-compute parlay leg counts in one query, attaching to each bet.

    Prevents an N+1 query in ``Bet.display_label`` which otherwise issues
    a COUNT per parlay bet when ``_parlay_legs_count`` is not set.
    """
    parlay_ids = {b.parlay_id for b in bets if b.is_parlay and b.parlay_id}
    if not parlay_ids:
        return
    counts = dict(
        db.session.query(Bet.parlay_id, func.count(Bet.id))
        .filter(Bet.parlay_id.in_(parlay_ids))
        .group_by(Bet.parlay_id)
        .all()
    )
    for b in bets:
        if b.is_parlay and b.parlay_id:
            b._parlay_legs_count = counts.get(b.parlay_id, 1)


def _get_cached_plays() -> tuple:
    """Return (top_plays, best_parlay) derived from the shared score cache."""
    top_plays = []
    best_parlay = None
    try:
        from app.services.score_cache import get_todays_scores
        from app.services.value_detector import ValueDetector
        all_scores = get_todays_scores()
        top_plays = ValueDetector.filter_plays(all_scores, min_edge=0.08)[:5]
        best_parlay = ValueDetector().recommend_best_parlay(
            scores=all_scores,
            min_edge=0.08,
            min_odds=100,
            max_odds=200,
            min_legs=2,
            max_legs=3,
        )
    except Exception as exc:
        logger.debug("Top plays unavailable: %s", exc)

    return top_plays, best_parlay


@main.route('/dashboard/settings', methods=['POST'])
@login_required
def dashboard_settings():
    raw_unit_size = (request.form.get('unit_size') or '').strip()
    if raw_unit_size == '':
        current_user.unit_size = None
        db.session.commit()
        flash('Unit size cleared.', 'success')
        return redirect(url_for('main.dashboard'))

    try:
        unit_size = float(raw_unit_size)
    except ValueError:
        flash('Unit size must be a number.', 'danger')
        return redirect(url_for('main.dashboard'))

    if unit_size <= 0:
        flash('Unit size must be greater than zero.', 'danger')
        return redirect(url_for('main.dashboard'))

    current_user.unit_size = unit_size
    db.session.commit()
    flash('Unit size saved.', 'success')
    return redirect(url_for('main.dashboard'))
