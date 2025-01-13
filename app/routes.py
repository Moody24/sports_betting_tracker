from flask import Blueprint, render_template, request, redirect, url_for
from app import db
from app.models import User, Bet, Match

main = Blueprint('main', __name__)

@main.route('/')
def home():
    return render_template('home.html')

@main.route('/bets', methods=['GET', 'POST'])
def manage_bets():
    if request.method == 'POST':
        # Logic to handle bet submission
        pass
    bets = Bet.query.all()
    return render_template('bets.html', bets=bets)
