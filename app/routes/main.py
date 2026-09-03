import logging
from collections import defaultdict
from xml.sax.saxutils import escape

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, fresh_login_required, login_required
from sqlalchemy import func, case, text

from app import db, csrf, limiter
from app.enums import Outcome
from app.models import Bet, compute_bets_net_pl, compute_bets_wagered
from app.public_pages import HOME_PAGE, PUBLIC_PAGE_BY_ENDPOINT, PUBLIC_PAGES

logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

UX_TELEMETRY_EVENTS = frozenset({
    'nba_today_refresh_error',
    'nba_today_refresh_retry',
    'nba_today_refresh_started',
    'nba_today_refresh_success',
    'prop_analysis_quick_add_parlay',
    'prop_analysis_quick_add_single',
    'prop_analysis_refresh_error',
    'prop_analysis_refresh_started',
    'prop_analysis_refresh_success',
    'stat_analysis_add_to_parlay',
    'stat_analysis_refresh_error',
    'stat_analysis_refresh_started',
    'stat_analysis_refresh_success',
    'unified_slip_no_games',
    'unified_slip_refresh_error',
    'unified_slip_refresh_started',
    'unified_slip_refresh_success',
    'unified_slip_submit_error',
    'unified_slip_submit_network_error',
    'unified_slip_submit_started',
    'unified_slip_submit_success',
})


def _sanitize_ux_meta(event: str, raw_meta) -> dict:
    if not isinstance(raw_meta, dict):
        return {}

    meta = {}
    if event == 'stat_analysis_add_to_parlay':
        side = str(raw_meta.get('side') or '').lower()
        if side in {'over', 'under'}:
            meta['side'] = side
    elif event in {
        'prop_analysis_quick_add_parlay',
        'unified_slip_submit_started',
        'unified_slip_submit_success',
    }:
        try:
            legs = int(raw_meta.get('legs'))
        except (TypeError, ValueError):
            legs = 0
        if 1 <= legs <= 20:
            meta['legs'] = legs
    return meta


def _get_model2_probe() -> dict:
    from app.services.pick_quality_model import get_model_runtime_probe
    return get_model_runtime_probe()


@main.route('/ready')
def ready():
    """Readiness endpoint that verifies dependencies like the database."""
    from datetime import datetime, timezone
    from app.models import TeamDefenseSnapshot
    try:
        db.session.execute(text('SELECT 1'))
    except Exception as exc:
        logger.error('Health check failed: %s', exc)
        return jsonify(status='unhealthy', database='disconnected'), 503

    # Staleness check for defense data
    defense_age_hours = None
    defense_data_stale = True
    try:
        latest_defense = (
            TeamDefenseSnapshot.query
            .order_by(TeamDefenseSnapshot.fetched_at.desc())
            .first()
        )
        if latest_defense and latest_defense.fetched_at:
            fetched = latest_defense.fetched_at
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            defense_age_hours = round(
                (datetime.now(timezone.utc) - fetched).total_seconds() / 3600, 2
            )
            defense_data_stale = defense_age_hours > 24
    except Exception as exc:
        logger.warning('Defense staleness check failed: %s', exc)

    return jsonify(
        status='healthy',
        database='connected',
        defense_data_age_hours=defense_age_hours,
        defense_data_stale=defense_data_stale,
    ), 200


@main.route('/ready/model2')
def ready_model2():
    """Readiness endpoint for Model 2 artifact resolution and loadability."""
    try:
        db.session.execute(text('SELECT 1'))
    except Exception as exc:
        logger.error('Model2 readiness failed (DB): %s', exc)
        return jsonify(status='unhealthy', database='disconnected', model2={'model_loadable': False}), 503

    try:
        probe = _get_model2_probe()
    except Exception as exc:
        logger.error('Model2 readiness failed: %s', exc)
        return jsonify(
            status='unhealthy',
            database='connected',
            model2={'model_loadable': False, 'reason': 'probe_exception'},
        ), 503

    http_status = 200 if probe.get('model_loadable') else 503
    return jsonify(
        status='healthy' if http_status == 200 else 'unhealthy',
        database='connected',
        model2=probe,
    ), http_status


@main.route('/')
def home():
    return render_template('home.html', page=HOME_PAGE)


def _render_public_page():
    return render_template(
        'public_page.html',
        page=PUBLIC_PAGE_BY_ENDPOINT[request.endpoint],
    )


@main.route('/methodology')
def methodology():
    return _render_public_page()


@main.route('/responsible-gambling')
def responsible_gambling():
    return _render_public_page()


@main.route('/privacy')
def privacy():
    return _render_public_page()


@main.route('/terms')
def terms():
    return _render_public_page()


@main.route('/data-sources')
def data_sources():
    return _render_public_page()


@main.route('/about')
def about():
    return _render_public_page()


@main.route('/robots.txt')
def robots_txt():
    if not current_app.config['DEPLOYMENT_IS_PRODUCTION']:
        body = 'User-agent: *\nDisallow: /\n'
    else:
        base_url = current_app.config['PUBLIC_BASE_URL']
        body = (
            'User-agent: *\n'
            'Allow: /\n'
            'Disallow: /auth/\n'
            'Disallow: /bets\n'
            'Disallow: /dashboard\n'
            'Disallow: /nba/\n'
            'Disallow: /health\n'
            'Disallow: /ready\n'
            'Disallow: /telemetry/\n'
            f'Sitemap: {base_url}/sitemap.xml\n'
        )
    return Response(body, content_type='text/plain; charset=utf-8')


@main.route('/sitemap.xml')
def sitemap_xml():
    base_url = current_app.config['PUBLIC_BASE_URL']
    entries = '\n'.join(
        '  <url>'
        f'<loc>{escape(base_url + page.path)}</loc>'
        f'<lastmod>{page.last_modified}</lastmod>'
        '</url>'
        for page in (HOME_PAGE, *PUBLIC_PAGES)
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{entries}\n'
        '</urlset>\n'
    )
    return Response(body, content_type='application/xml; charset=utf-8')


@main.route('/telemetry/ux', methods=['POST'])
@csrf.exempt
@limiter.limit("60 per minute")
def ux_telemetry():
    """Receive a fixed, non-sensitive UX event vocabulary.

    This endpoint is CSRF-exempt because sendBeacon cannot supply the CSRF form
    token and the endpoint performs no user or domain-state mutation. The strict
    event/meta allowlists and rate limit are the abuse boundary.
    """
    if not request.is_json:
        return jsonify(error='application/json required'), 415
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error='invalid JSON payload'), 400
    event = str(payload.get('event') or '').strip()[:64]
    if event not in UX_TELEMETRY_EVENTS:
        return jsonify(error='unsupported event'), 400

    page = str(payload.get('page') or '/').strip()[:120]
    if not page.startswith('/') or page.startswith('//'):
        return jsonify(error='invalid page'), 400
    meta = _sanitize_ux_meta(event, payload.get('meta'))

    uid = current_user.id if current_user.is_authenticated else None
    logger.info('ux_event event=%s page=%s user_id=%s meta=%s', event, page, uid, meta)
    return ('', 204)


@main.route('/dashboard')
@login_required
def dashboard():
    uid = current_user.id

    # ── Aggregate stats in a single SQL query ─────────────────────────
    agg = db.session.query(
        func.count(Bet.id).label('total'),
        func.coalesce(func.sum(case((Bet.outcome == Outcome.WIN.value, 1), else_=0)), 0).label('wins'),
        func.coalesce(func.sum(case((Bet.outcome == Outcome.LOSE.value, 1), else_=0)), 0).label('losses'),
        func.coalesce(func.sum(case((Bet.outcome == Outcome.PUSH.value, 1), else_=0)), 0).label('pushes'),
    ).filter_by(user_id=uid).one()

    total_bets = int(agg.total)
    wins = int(agg.wins)
    losses = int(agg.losses)
    pushes = int(agg.pushes)
    wagered = float(current_user.total_amount_wagered())

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
    # ROI denominator: parlay-aware wagered on graded bets only.
    wagered_graded = compute_bets_wagered(graded_bets)
    roi = (units_won / wagered_graded * 100) if wagered_graded else 0
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
        'pushes': pushes,
        'pending': max(total_bets - wins - losses - pushes, 0),
        'wagered': wagered,
        'net': units_won,
        'units_won': units_won,
        'roi': round(roi, 1),
        'win_pct': round(win_pct, 1),
        'current_streak': current_streak,
    }

    # ── Last 30 resolved bets for sparkline / ribbon (oldest first) ──
    resolved_bets = list(reversed(graded_bets[:30]))

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
        resolved_bets=resolved_bets,
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
@fresh_login_required
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
