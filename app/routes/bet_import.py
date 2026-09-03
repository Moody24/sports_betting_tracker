"""Bet import routes: quick-add, parlay builder, OCR screenshot parser."""

import io
import json
import logging
import re
from datetime import datetime, timezone

from flask import request, jsonify, url_for, flash, redirect
from flask_login import login_required, current_user

from app import db
from app.enums import BetSource, Outcome
from app.models import Bet, PickContext
from app.services.feature_engine import build_pick_context_features
from app.services.stats_service import find_player_id
from app.services.bet_placement_service import (
    BetPlacementError,
    build_manual_parlay_bets,
    parse_optional_units,
    parse_stake,
    persist_new_bets,
)

logger = logging.getLogger(__name__)

_OCR_STAT_PATTERNS = (
    (r'\b(?:pra|points?\s*\+\s*rebounds?\s*\+\s*assists?'
     r'|pts\s*\+\s*reb\s*\+\s*ast)\b',
     'player_points_rebounds_assists'),
    (r'\bpoints?\b', 'player_points'),
    (r'\brebs?\b|\brebounds?\b', 'player_rebounds'),
    (r'\basts?\b|\bassists?\b', 'player_assists'),
    (r'\b3[- ]?pointers?\b|\bthrees?\b|\b3pts?\b', 'player_threes'),
    (r'\bblocks?\b|\bblks?\b', 'player_blocks'),
    (r'\bsteals?\b|\bstls?\b', 'player_steals'),
)
_OCR_NON_PLAYER_LABELS = {
    'Over', 'Under', 'Game', 'Player', 'Total', 'Points', 'Rebounds',
    'Assists', 'Parlay', 'Bet', 'Same', 'Alternate', 'Combo', 'Spread',
}

# ── Helpers ───────────────────────────────────────────────────────────────


def _first_valid_number(pattern: str, text: str, minimum: float,
                        maximum: float, cast=float):
    matches = re.findall(pattern, text)
    if not matches:
        return None
    value = cast(matches[0])
    return value if minimum <= value <= maximum and value != 0 else None


def _ocr_teams(text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r'([A-Za-z][A-Za-z\s]{2,25})\s+(?:@|vs\.?)\s+'
        r'([A-Za-z][A-Za-z\s]{2,25})',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    teams = tuple(group.strip() for group in match.groups())
    if all(3 < len(team) < 30 for team in teams):
        return teams
    return None, None


def _ocr_prop_type(text: str) -> str | None:
    for pattern, stat_type in _OCR_STAT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return stat_type
    return None


def _ocr_player_name(text: str) -> str | None:
    pattern = r'^([A-Z][a-z]+(?:\s+[A-Z][a-z\']+)+)'
    for match in re.finditer(pattern, text, re.MULTILINE):
        candidate = match.group(1).strip()
        if candidate not in _OCR_NON_PLAYER_LABELS \
                and len(candidate.split()) >= 2:
            return candidate
    return None

def _parse_ocr_text(text: str) -> dict:
    """Parse raw OCR text from a bet screenshot into structured fields."""
    result: dict = {
        'player_name': None,
        'prop_type': None,
        'bet_type': None,
        'prop_line': None,
        'american_odds': None,
        'stake': None,
        'team_a': None,
        'team_b': None,
        'legs': [],
    }

    ou_match = re.search(r'\b(over|under)\s+([\d]+\.?\d*)\b', text, re.IGNORECASE)
    if ou_match:
        result['bet_type'] = ou_match.group(1).lower()
        raw_line = float(ou_match.group(2))
        if 0 < raw_line < 200:  # reject impossible lines (negative, zero, or absurd)
            result['prop_line'] = raw_line
    result['american_odds'] = _first_valid_number(
        r'([+\-]\d{3,4})', text, -2500, 2500, int,
    )
    result['stake'] = _first_valid_number(
        r'\$\s*([\d]+\.?\d*)', text, 0, 10000,
    )
    result['team_a'], result['team_b'] = _ocr_teams(text)
    result['prop_type'] = _ocr_prop_type(text)
    result['player_name'] = _ocr_player_name(text)
    return result


# ── Routes ────────────────────────────────────────────────────────────────

@login_required
def quick_add_bet():
    """Create a single straight bet from a dashboard top-play row."""
    player = (request.form.get('player') or '').strip()[:100]
    prop_type = (request.form.get('prop_type') or '').strip()[:40]
    prop_line = request.form.get('prop_line', type=float)
    bet_type = (request.form.get('bet_type') or 'over').strip()[:20]
    american_odds = request.form.get('american_odds', type=int)
    team_a = (request.form.get('team_a') or 'Away').strip()[:80]
    team_b = (request.form.get('team_b') or 'Home').strip()[:80]
    match_date_str = (request.form.get('match_date') or '').strip()
    game_id = (request.form.get('game_id') or '').strip()[:50]
    stake = request.form.get('stake', type=float)

    if not stake or stake <= 0:
        flash('Enter a stake amount.', 'danger')
        return redirect(url_for('main.dashboard'))

    try:
        match_dt = datetime.strptime(match_date_str, '%Y-%m-%d') if match_date_str else datetime.now(timezone.utc)
    except ValueError:
        match_dt = datetime.now(timezone.utc)

    bet_obj = Bet(
        user_id=current_user.id,
        team_a=team_a,
        team_b=team_b,
        match_date=match_dt,
        bet_amount=stake,
        outcome=Outcome.PENDING.value,
        american_odds=american_odds,
        is_parlay=False,
        source=BetSource.NBA_PROPS.value,
        bet_type=bet_type,
        player_name=player or None,
        prop_type=prop_type or None,
        prop_line=prop_line,
        external_game_id=game_id or None,
    )
    db.session.add(bet_obj)
    db.session.flush()

    player_id = find_player_id(player) if player else None
    if player_id:
        projected_stat = request.form.get('projection', type=float) or 0.0
        projected_edge = request.form.get('edge', type=float) or 0.0
        confidence_tier = (request.form.get('confidence_tier') or 'slight').strip()
        ctx = build_pick_context_features(
            player_name=player,
            player_id=str(player_id),
            prop_type=prop_type,
            prop_line=float(prop_line or 0),
            american_odds=int(american_odds or -110),
            projected_stat=projected_stat,
            projected_edge=projected_edge,
            confidence_tier=confidence_tier,
            opponent_name='',
            team_name='',
            is_home=True,
        )
        db.session.add(PickContext(
            bet_id=bet_obj.id,
            context_json=json.dumps(ctx),
            projected_stat=projected_stat,
            projected_edge=projected_edge,
            confidence_tier=confidence_tier,
        ))

    db.session.commit()
    flash(f'Added: {player} {bet_type.capitalize()} {prop_line}', 'success')
    return redirect(url_for('main.dashboard'))


@login_required
def quick_add_parlay():
    """Create a parlay from the dashboard Best Play of the Day legs."""
    stake = request.form.get('stake', type=float)
    units = request.form.get('units', type=float)
    legs_json = request.form.get('legs', '')

    unit_size = current_user.unit_size
    if stake is not None and units is None and unit_size:
        units = round(stake / unit_size, 4)
    elif units is not None and stake is None and unit_size:
        stake = round(units * unit_size, 2)

    if not stake or stake <= 0:
        flash('Enter a stake amount, or configure your unit size and enter units.', 'danger')
        return redirect(url_for('main.dashboard'))

    try:
        legs_data = json.loads(legs_json)
    except (ValueError, TypeError):
        flash('Invalid parlay data.', 'danger')
        return redirect(url_for('main.dashboard'))

    if len(legs_data) < 2:
        flash('A parlay needs at least 2 legs.', 'danger')
        return redirect(url_for('main.dashboard'))
    parlay_id = Bet.generate_parlay_id()
    for leg in legs_data:
        player = (leg.get('player') or '')[:100]
        prop_type = (leg.get('prop_type') or '')[:40]
        prop_line_val = leg.get('line')
        bet_type = (leg.get('side') or 'over')[:20]
        american_odds = leg.get('odds')
        team_a = (leg.get('away_team') or 'Away')[:80]
        team_b = (leg.get('home_team') or 'Home')[:80]
        match_date_str = leg.get('match_date') or ''
        game_id = (leg.get('game_id') or '')[:80]

        try:
            match_dt = datetime.strptime(match_date_str, '%Y-%m-%d') if match_date_str else datetime.now(timezone.utc)
        except ValueError:
            match_dt = datetime.now(timezone.utc)

        db.session.add(Bet(
            user_id=current_user.id,
            team_a=team_a,
            team_b=team_b,
            match_date=match_dt,
            bet_amount=stake,
            units=units,
            outcome=Outcome.PENDING.value,
            american_odds=int(american_odds) if american_odds is not None else None,
            is_parlay=True,
            parlay_id=parlay_id,
            source=BetSource.NBA_PROPS.value,
            bet_type=bet_type,
            player_name=player or None,
            prop_type=prop_type or None,
            prop_line=float(prop_line_val) if prop_line_val is not None else None,
            external_game_id=game_id or None,
        ))

    db.session.flush()
    leg_count = len(legs_data)
    legs_added = Bet.query.filter_by(parlay_id=parlay_id).all()
    for leg_obj in legs_added:
        leg_obj.parlay_leg_count = leg_count
    db.session.commit()
    flash(f'Added {len(legs_data)}-leg parlay to your bets.', 'success')
    return redirect(url_for('main.dashboard'))


@login_required
def manual_parlay():
    """Place a manually-built parlay from the bet builder."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    legs = data.get("legs", [])
    outcome = data.get("outcome", Outcome.PENDING.value)
    if outcome != Outcome.PENDING.value:
        return jsonify({"success": False, "message": "New bets must be PENDING"}), 400

    if not legs:
        return jsonify({"success": False, "message": "Add at least one leg"}), 400

    try:
        stake = parse_stake(data.get('stake'))
        created_bets = build_manual_parlay_bets(
            user_id=current_user.id,
            legs=legs,
            stake=stake,
            units=parse_optional_units(data.get('units')),
            outcome=outcome,
        )
    except BetPlacementError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400

    persist_new_bets(created_bets)

    return jsonify({
        "success": True,
        "message": f"Parlay with {len(legs)} leg(s) saved — ${stake:.2f} wagered!",
        "redirect": url_for('bet.place_bet'),
    })


@login_required
def ocr_screenshot():
    """Accept a PNG/JPG screenshot, OCR it, and return parsed bet fields as JSON."""
    if 'screenshot' not in request.files:
        return jsonify({"success": False, "message": "No file provided"}), 400

    file = request.files['screenshot']
    if not file or not file.filename:
        return jsonify({"success": False, "message": "No file selected"}), 400

    allowed_ext = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    if not file.filename.lower().endswith(allowed_ext):
        return jsonify({"success": False, "message": "Only PNG/JPG/WEBP images are supported"}), 400

    allowed_mimetypes = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'}
    if file.content_type not in allowed_mimetypes:
        return jsonify({"success": False, "message": "Invalid file type"}), 400

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return jsonify({
            "success": False,
            "message": (
                'OCR requires pytesseract + Pillow. '
                'Run: pip install pytesseract Pillow  '
                'and install the tesseract-ocr system package.'
            )
        }), 503

    try:
        img_bytes = file.read()
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        w, h = img.size
        if w < 800:
            scale = 800 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        raw_text = pytesseract.image_to_string(img, config='--psm 3')
        parsed = _parse_ocr_text(raw_text)
        parsed['raw_text'] = raw_text[:3000]
        return jsonify({'success': True, **parsed})

    except Exception as exc:
        logger.error("OCR processing failed: %s", exc)
        return jsonify({"success": False, "message": "OCR processing failed. Please try a clearer image."}), 500
