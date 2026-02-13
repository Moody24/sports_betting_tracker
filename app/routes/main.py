from collections import defaultdict

from flask import Blueprint, render_template
from flask_login import current_user, login_required
from flask_login import login_required, current_user

from app.models import Bet

main = Blueprint('main', __name__)


@main.route('/')
def home():
    return render_template('home.html')


@main.route('/dashboard')
@login_required
def dashboard():
    user_bets = (
        Bet.query.filter_by(user_id=current_user.id)
        .order_by(Bet.created_at.desc())
        .all()
    )
    recent_bets = user_bets[:7]

    total_bets = current_user.total_bets()
    wins = current_user.total_wins()
    losses = current_user.total_losses()
    wagered = current_user.total_amount_wagered()
    units_won = current_user.net_profit_loss()
    roi = (units_won / wagered * 100) if wagered else 0
    graded_bets = wins + losses
    win_pct = (wins / graded_bets * 100) if graded_bets else 0

    streak = 0
    streak_type = 'No streak'
    for bet in user_bets:
        if bet.outcome not in {'win', 'lose'}:
            continue
        if streak == 0:
            streak = 1
            streak_type = bet.outcome
        elif bet.outcome == streak_type:
            streak += 1
        else:
            break
    current_streak = f"{streak} {streak_type.title()}" if streak else 'No streak'

    grouped_units = defaultdict(float)
    for bet in reversed(recent_bets):
        label = bet.match_date.strftime('%b %d')
        grouped_units[label] += bet.profit_loss()

    chart_labels = list(grouped_units.keys())
    chart_values = [round(v, 2) for v in grouped_units.values()]

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

    return render_template(
        'dashboard.html',
        stats=stats,
        recent_bets=recent_bets,
        chart_labels=chart_labels,
        chart_values=chart_values,
    )
    recent_bets = (
        Bet.query.filter_by(user_id=current_user.id)
        .order_by(Bet.created_at.desc())
        .limit(5)
        .all()
    )

    stats = {
        'total_bets': current_user.total_bets(),
        'wins': current_user.total_wins(),
        'losses': current_user.total_losses(),
        'wagered': current_user.total_amount_wagered(),
        'net': current_user.net_profit_loss(),
    }

    return render_template('dashboard.html', stats=stats, recent_bets=recent_bets)
