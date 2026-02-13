from flask import Blueprint, render_template
from flask_login import login_required, current_user

from app.models import Bet

main = Blueprint('main', __name__)


@main.route('/')
def home():
    return render_template('home.html')


@main.route('/dashboard')
@login_required
def dashboard():
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
