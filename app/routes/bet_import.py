"""Bet import routes: quick-add, parlay builder, OCR screenshot parser."""

import io
import json
import logging
import re
from datetime import datetime, timezone

from flask import request, jsonify, url_for, flash, redirect
from flask_login import login_required, current_user

from app import db
from app.enums import BetSource, BetType, Outcome
from app.models import Bet, PickContext
from app.services.projection_engine import ProjectionEngine
from app.services.value_detector import ValueDetector
from app.services.feature_engine import build_pick_context_features
from app.services.stats_service import find_player_id
from app.routes.bet_crud import _create_pick_context_for_bet

logger = logging.getLogger(__name__)

_POSITION_ORDER = {'PG': 0, 'SG': 1, 'SF': 2, 'PF': 3, 'C': 4}


# ── Helpers ───────────────────────────────────────────────────────────────

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

    odds_matches = re.findall(r'([+\-]\d{3,4})', text)
    if odds_matches:
        raw_odds = int(odds_matches[0])
        if raw_odds != 0 and -2500 <= raw_odds <= 2500:  # valid American odds range
            result['american_odds'] = raw_odds

    stake_matches = re.findall(r'\$\s*([\d]+\.?\d*)', text)
    if stake_matches:
        raw_stake = float(stake_matches[0])
        if 0 < raw_stake <= 10000:  # reject zero, negative, or implausible stakes
            result['stake'] = raw_stake

    vs_match = re.search(
        r'([A-Za-z][A-Za-z\s]{2,25})\s+(?:@|vs\.?)\s+([A-Za-z][A-Za-z\s]{2,25})',
        text, re.IGNORECASE,
    )
    if vs_match:
        t1 = vs_match.group(1).strip()
        t2 = vs_match.group(2).strip()
        if 3 < len(t1) < 30 and 3 < len(t2) < 30:
            result['team_a'] = t1
            result['team_b'] = t2

    stat_map = [
        (r'\b(?:pra|points?\s*\+\s*rebounds?\s*\+\s*assists?|pts\s*\+\s*reb\s*\+\s*ast)\b', 'player_points_rebounds_assists'),
        (r'\bpoints?\b', 'player_points'),
        (r'\brebs?\b|\brebounds?\b', 'player_rebounds'),
        (r'\basts?\b|\bassists?\b', 'player_assists'),
        (r'\b3[- ]?pointers?\b|\bthrees?\b|\b3pts?\b', 'player_threes'),
        (r'\bblocks?\b|\bblks?\b', 'player_blocks'),
        (r'\bsteals?\b|\bstls?\b', 'player_steals'),
    ]
    for pattern, stat_type in stat_map:
        if re.search(pattern, text, re.IGNORECASE):
            result['prop_type'] = stat_type
            break

    non_player = {
        'Over', 'Under', 'Game', 'Player', 'Total', 'Points', 'Rebounds',
        'Assists', 'Parlay', 'Bet', 'Same', 'Alternate', 'Combo', 'Spread',
    }
    for m in re.finditer(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z\']+)+)', text, re.MULTILINE):
        candidate = m.group(1).strip()
        if candidate not in non_player and len(candidate.split()) >= 2:
            result['player_name'] = candidate
            break

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
        return jsonify({"error": "Invalid request"}), 400

    legs = data.get("legs", [])
    outcome = data.get("outcome", Outcome.PENDING.value)
    if outcome != Outcome.PENDING.value:
        return jsonify({"success": False, "message": "New bets must be PENDING"}), 400

    if not legs:
        return jsonify({"error": "Add at least one leg"}), 400

    try:
        stake = float(data.get("stake") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Stake must be a number"}), 400
    if stake <= 0:
        return jsonify({"error": "Stake must be greater than zero"}), 400

    units_val = None
    if data.get("units") is not None:
        try:
            parsed_units = float(data.get("units"))
            if parsed_units > 0:
                units_val = parsed_units
        except (TypeError, ValueError):
            units_val = None
    parlay_id = Bet.generate_parlay_id()

    errors = []
    created_bets: list[Bet] = []
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            errors.append(f"Leg {i + 1}: must be an object")
            continue
        if not leg.get("team_a") or not leg.get("team_b"):
            errors.append(f"Leg {i + 1}: team_a and team_b are required")
            continue

        try:
            match_date = datetime.strptime(leg.get("match_date", ""), "%Y-%m-%d")
        except ValueError:
            match_date = datetime.now(timezone.utc)

        bet_type = leg.get("bet_type", BetType.MONEYLINE.value)
        player_name = str(leg.get("player_name") or "")[:100] or None
        prop_type = str(leg.get("prop_type") or "")[:40] or None
        prop_line = None
        if leg.get("prop_line"):
            try:
                prop_line = float(leg["prop_line"])
            except (ValueError, TypeError):
                errors.append(f"Leg {i + 1}: prop_line must be a number")
                continue
            if not (-50 < prop_line < 100):
                errors.append(f"Leg {i + 1}: prop_line out of range (-50, 100)")
                continue

        ou_line = None
        if bet_type in (BetType.OVER.value, BetType.UNDER.value) and not player_name:
            try:
                ou_line = float(leg["over_under_line"]) if leg.get("over_under_line") else None
            except (ValueError, TypeError):
                pass

        leg_odds = leg.get("american_odds", leg.get("odds"))
        parsed_odds = None
        if leg_odds not in (None, ""):
            try:
                parsed_odds = int(leg_odds)
            except (TypeError, ValueError):
                errors.append(f"Leg {i + 1}: american_odds must be an integer")
                continue
            if not (-5000 <= parsed_odds <= 5000):
                errors.append(f"Leg {i + 1}: american_odds out of range (-5000, 5000)")
                continue
            if parsed_odds == 0:
                parsed_odds = None

        bet_obj = Bet(
            user_id=current_user.id,
            team_a=str(leg["team_a"])[:80],
            team_b=str(leg["team_b"])[:80],
            match_date=match_date,
            bet_amount=stake,
            units=units_val,
            outcome=outcome,
            american_odds=parsed_odds,
            bet_type=bet_type,
            over_under_line=ou_line,
            prop_line=prop_line,
            player_name=player_name,
            prop_type=prop_type,
            picked_team=str(leg.get("picked_team") or "")[:80] or None,
            external_game_id=leg.get("game_id") or None,
            is_parlay=True,
            parlay_id=parlay_id,
            source=BetSource.MANUAL.value,
        )
        db.session.add(bet_obj)
        created_bets.append(bet_obj)

    if errors:
        db.session.rollback()
        return jsonify({"error": "; ".join(errors)}), 400

    db.session.flush()
    detector = ValueDetector(ProjectionEngine())
    for bet_obj in created_bets:
        _create_pick_context_for_bet(
            bet_obj=bet_obj,
            detector=detector,
            selected_odds=bet_obj.american_odds,
        )

    db.session.commit()

    if created_bets:
        leg_count = len(created_bets)
        for leg_obj in created_bets:
            leg_obj.parlay_leg_count = leg_count
        db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Parlay with {len(legs)} leg(s) saved — ${stake:.2f} wagered!",
        "redirect": url_for('bet.place_bet'),
    })


@login_required
def ocr_screenshot():
    """Accept a PNG/JPG screenshot, OCR it, and return parsed bet fields as JSON."""
    if 'screenshot' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['screenshot']
    if not file or not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    allowed_ext = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    if not file.filename.lower().endswith(allowed_ext):
        return jsonify({'error': 'Only PNG/JPG/WEBP images are supported'}), 400

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return jsonify({
            'error': (
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
        return jsonify({'error': 'OCR processing failed. Please try a clearer image.'}), 500
