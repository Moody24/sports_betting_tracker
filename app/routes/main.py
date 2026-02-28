import logging
from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash
from flask_login import current_user, login_required
from sqlalchemy import func, case, text

from app import db
from app.enums import Outcome
from app.models import Bet

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

    # ── Net P/L (needs per-bet odds calculation, but only graded bets) ─
    units_won = current_user.net_profit_loss()
    roi = (units_won / wagered * 100) if wagered else 0
    graded_count = wins + losses
    win_pct = (wins / graded_count * 100) if graded_count else 0

    # ── Streak (only need recent graded bets until streak breaks) ─────
    streak = 0
    streak_type = 'No streak'
    streak_bets = (
        Bet.query.filter_by(user_id=uid)
        .filter(Bet.outcome.in_([Outcome.WIN.value, Outcome.LOSE.value]))
        .order_by(Bet.created_at.desc())
        .all()
    )
    for b in streak_bets:
        if streak == 0:
            streak = 1
            streak_type = b.outcome
        elif b.outcome == streak_type:
            streak += 1
        else:
            break
    current_streak = f"{streak} {streak_type.title()}" if streak else 'No streak'

    # ── Daily P/L chart (recent bets only) ────────────────────────────
    grouped_units = defaultdict(float)
    for b in reversed(recent_bets):
        label = b.match_date.strftime('%b %d')
        grouped_units[label] += b.profit_loss()

    chart_labels = list(grouped_units.keys())
    chart_values = [round(v, 2) for v in grouped_units.values()]

    # ── Cumulative P/L (last 30 graded bets, oldest first) ───────────
    cumul_bets = (
        Bet.query.filter_by(user_id=uid)
        .filter(Bet.outcome.in_([Outcome.WIN.value, Outcome.LOSE.value]))
        .order_by(Bet.match_date.desc())
        .limit(30)
        .all()
    )
    cumul_bets.reverse()  # oldest first
    cumulative = 0.0
    cumul_labels = []
    cumul_values = []
    for b in cumul_bets:
        cumulative = round(cumulative + b.profit_loss(), 2)
        cumul_labels.append(b.match_date.strftime('%b %d'))
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

    # ── Today's top plays from the analysis engine ──────────────────
    top_plays = []
    best_parlay = None
    try:
        from app.services.projection_engine import ProjectionEngine
        from app.services.value_detector import ValueDetector
        engine = ProjectionEngine()
        detector = ValueDetector(engine)
        all_scores = detector.score_all_todays_props()
        top_plays = detector.filter_plays(all_scores, min_edge=0.08)[:5]
        best_parlay = detector.recommend_best_parlay(
            scores=all_scores,
            min_edge=0.08,
            min_odds=100,
            max_odds=200,
            min_legs=2,
            max_legs=3,
        )
    except Exception as exc:
        logger.debug("Top plays unavailable: %s", exc)

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
