import calendar
from collections import defaultdict
from datetime import date

from flask import Blueprint, render_template
from flask_login import current_user, login_required

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
    grouped_staked = defaultdict(float)
    for bet in reversed(user_bets):
        label = bet.match_date.strftime('%b %d')
        grouped_units[label] += bet.profit_loss()
        grouped_staked[label] += bet.bet_amount

    chart_labels = list(grouped_units.keys())[-14:]
    chart_values = [round(grouped_units[label], 2) for label in chart_labels]

    # Calendar for current month: red = losing day, green = profitable day
    today = user_bets[0].match_date.date() if user_bets else None
    current_year = today.year if today else date.today().year
    current_month = today.month if today else date.today().month

    month_days = calendar.monthrange(current_year, current_month)[1]
    day_stats = defaultdict(lambda: {'staked': 0.0, 'profit': 0.0})

    for bet in user_bets:
        date_value = bet.match_date.date()
        if date_value.year == current_year and date_value.month == current_month:
            day_stats[date_value.day]['staked'] += float(bet.bet_amount)
            day_stats[date_value.day]['profit'] += float(bet.profit_loss())

    calendar_cells = []
    first_weekday = calendar.monthrange(current_year, current_month)[0]
    for _ in range(first_weekday):
        calendar_cells.append(None)

    for day in range(1, month_days + 1):
        stats = day_stats[day]
        profit = round(stats['profit'], 2)
        if profit > 0:
            tone = 'profit'
        elif profit < 0:
            tone = 'loss'
        else:
            tone = 'neutral'

        calendar_cells.append(
            {
                'day': day,
                'staked': round(stats['staked'], 2),
                'profit': profit,
                'tone': tone,
            }
        )

    while len(calendar_cells) % 7 != 0:
        calendar_cells.append(None)

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
        calendar_cells=calendar_cells,
        calendar_month=calendar.month_name[current_month],
        calendar_year=current_year,
        weekday_labels=['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
    )
