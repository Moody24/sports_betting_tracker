"""Bet blueprint — thin shell that assembles sub-module routes.

All route logic lives in the four sub-modules:
  bet_crud.py     — list, create, edit, grade, delete, export
  nba_live.py     — today's games, prop progress, betslip placement
  nba_analysis.py — all-props browser, analysis dashboard, stat analysis
  bet_import.py   — quick-add, parlay builder, OCR screenshot

url_for('bet.<endpoint>') works unchanged for every existing template
because all routes are registered here on the single 'bet' Blueprint.
"""

import logging

from flask import Blueprint

logger = logging.getLogger(__name__)

bet = Blueprint('bet', __name__)

# ── Import route handlers from sub-modules ────────────────────────────────
from app.routes.bet_crud import (       # noqa: E402
    place_bet, new_bet, edit_bet, delete_bet, grade_bet, export_bets,
)
from app.routes.nba_live import (       # noqa: E402
    nba_today, nba_update_results, nba_upcoming_games, nba_props,
    nba_prop_progress, nba_prop_progress_batch, nba_place_bets,
    _GAME_SUMMARY_CACHE, _PROP_PROGRESS_CACHE,              # re-exported for test teardown
)
from app.routes.nba_analysis import (   # noqa: E402
    nba_all_props, nba_analysis, nba_player_analysis, nba_stat_analysis,
)
from app.routes.bet_import import (     # noqa: E402
    quick_add_bet, quick_add_parlay, manual_parlay, ocr_screenshot,
)

# ── CRUD ──────────────────────────────────────────────────────────────────
bet.add_url_rule('/bets',                      view_func=place_bet)
bet.add_url_rule('/bets/new',                  view_func=new_bet,                  methods=['GET', 'POST'])
bet.add_url_rule('/bets/<int:bet_id>/edit',    view_func=edit_bet,                 methods=['POST'])
bet.add_url_rule('/delete_bet/<int:bet_id>',   view_func=delete_bet,               methods=['POST'])
bet.add_url_rule('/bets/<int:bet_id>/grade',   view_func=grade_bet,                methods=['POST'])
bet.add_url_rule('/bets/export',               view_func=export_bets)

# ── NBA Live ───────────────────────────────────────────────────────────────
bet.add_url_rule('/nba/today',                 view_func=nba_today)
bet.add_url_rule('/nba/update-results',        view_func=nba_update_results,       methods=['POST'])
bet.add_url_rule('/nba/upcoming-games',        view_func=nba_upcoming_games)
bet.add_url_rule('/nba/props/<espn_id>',       view_func=nba_props)
bet.add_url_rule('/nba/prop-progress/<espn_id>', view_func=nba_prop_progress)
bet.add_url_rule('/nba/prop-progress/batch',   view_func=nba_prop_progress_batch,  methods=['POST'])
bet.add_url_rule('/nba/place-bets',            view_func=nba_place_bets,           methods=['POST'])

# ── NBA Analysis ───────────────────────────────────────────────────────────
bet.add_url_rule('/nba/all-props',                          view_func=nba_all_props)
bet.add_url_rule('/nba/analysis',                           view_func=nba_analysis)
bet.add_url_rule('/nba/player-analysis/<player_name>',      view_func=nba_player_analysis)
bet.add_url_rule('/nba/stat-analysis',                      view_func=nba_stat_analysis)

# ── Import ─────────────────────────────────────────────────────────────────
bet.add_url_rule('/quick-add',           view_func=quick_add_bet,    methods=['POST'])
bet.add_url_rule('/quick-add-parlay',    view_func=quick_add_parlay, methods=['POST'])
bet.add_url_rule('/bets/parlay',         view_func=manual_parlay,    methods=['POST'])
bet.add_url_rule('/bets/ocr-screenshot', view_func=ocr_screenshot,   methods=['POST'])
