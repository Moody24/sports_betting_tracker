"""Explicit security and ownership policy for every HTTP endpoint.

The runtime URL map is tested against this catalog. Adding or changing a route
therefore requires a deliberate policy decision instead of inheriting an
accidental default.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutePolicy:
    methods: frozenset[str]
    path: str
    authentication: str
    csrf: str
    response: str
    owner: str
    rate_limit: str


def _policy(
    methods: str,
    path: str,
    authentication: str,
    csrf: str,
    response: str,
    owner: str,
    rate_limit: str = 'default',
) -> RoutePolicy:
    return RoutePolicy(
        methods=frozenset(methods.split(',')),
        path=path,
        authentication=authentication,
        csrf=csrf,
        response=response,
        owner=owner,
        rate_limit=rate_limit,
    )


ROUTE_POLICIES = {
    'main.home': _policy('GET', '/', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.about': _policy('GET', '/about', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.methodology': _policy('GET', '/methodology', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.responsible_gambling': _policy('GET', '/responsible-gambling', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.privacy': _policy('GET', '/privacy', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.terms': _policy('GET', '/terms', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.data_sources': _policy('GET', '/data-sources', 'optional', 'not-applicable', 'html', 'public-site'),
    'main.robots_txt': _policy('GET', '/robots.txt', 'optional', 'not-applicable', 'text', 'public-site'),
    'main.sitemap_xml': _policy('GET', '/sitemap.xml', 'optional', 'not-applicable', 'xml', 'public-site'),
    'favicon': _policy('GET', '/favicon.ico', 'optional', 'not-applicable', 'image-or-empty', 'platform'),
    'health': _policy('GET', '/health', 'optional', 'not-applicable', 'json', 'operations'),
    'main.ready': _policy('GET', '/ready', 'optional', 'not-applicable', 'json', 'operations'),
    'main.ready_model2': _policy('GET', '/ready/model2', 'optional', 'not-applicable', 'json', 'ml-operations'),
    'main.ux_telemetry': _policy('POST', '/telemetry/ux', 'optional', 'exempt-safe-event', 'empty-or-json-error', 'observability', '60/minute'),
    'auth.login': _policy('GET,POST', '/auth/login', 'anonymous', 'protected', 'html', 'identity', '10/minute'),
    'auth.logout': _policy('POST', '/auth/logout', 'optional', 'protected', 'redirect', 'identity'),
    'auth.register': _policy('GET,POST', '/auth/register', 'anonymous', 'protected', 'html', 'identity', '5/minute'),
    'main.dashboard': _policy('GET', '/dashboard', 'required', 'not-applicable', 'html', 'betting'),
    'main.dashboard_settings': _policy('POST', '/dashboard/settings', 'fresh-required', 'protected', 'redirect', 'identity'),
    'bet.place_bet': _policy('GET', '/bets', 'required', 'not-applicable', 'html', 'betting'),
    'bet.new_bet': _policy('GET,POST', '/bets/new', 'required', 'protected', 'html-or-redirect', 'betting'),
    'bet.edit_bet': _policy('POST', '/bets/<int:bet_id>/edit', 'required', 'protected', 'redirect', 'betting'),
    'bet.grade_bet': _policy('POST', '/bets/<int:bet_id>/grade', 'required', 'protected', 'redirect', 'betting'),
    'bet.delete_bet': _policy('POST', '/delete_bet/<int:bet_id>', 'required', 'protected', 'redirect', 'betting'),
    'bet.export_bets': _policy('GET', '/bets/export', 'required', 'not-applicable', 'csv', 'betting', '10/minute'),
    'bet.manual_parlay': _policy('POST', '/bets/parlay', 'required', 'protected', 'redirect', 'betting'),
    'bet.ocr_screenshot': _policy('POST', '/bets/ocr-screenshot', 'required', 'protected', 'json', 'betting'),
    'bet.quick_add_bet': _policy('POST', '/quick-add', 'required', 'protected', 'redirect', 'betting'),
    'bet.quick_add_parlay': _policy('POST', '/quick-add-parlay', 'required', 'protected', 'redirect', 'betting'),
    'bet.nba_today': _policy('GET', '/nba/today', 'required', 'not-applicable', 'html', 'nba'),
    'bet.nba_update_results': _policy('POST', '/nba/update-results', 'required', 'protected', 'redirect', 'nba'),
    'bet.nba_upcoming_games': _policy('GET', '/nba/upcoming-games', 'required', 'not-applicable', 'json', 'nba'),
    'bet.nba_props': _policy('GET', '/nba/props/<espn_id>', 'required', 'not-applicable', 'json', 'nba'),
    'bet.nba_prop_progress': _policy('GET', '/nba/prop-progress/<espn_id>', 'required', 'not-applicable', 'json', 'nba'),
    'bet.nba_prop_progress_batch': _policy('POST', '/nba/prop-progress/batch', 'required', 'protected', 'json', 'nba'),
    'bet.nba_place_bets': _policy('POST', '/nba/place-bets', 'required', 'protected', 'json', 'nba'),
    'bet.nba_all_props': _policy('GET', '/nba/all-props', 'required', 'not-applicable', 'json', 'nba', '6/minute'),
    'bet.nba_analysis': _policy('GET', '/nba/analysis', 'required', 'not-applicable', 'html', 'nba'),
    'bet.nba_player_analysis': _policy('GET', '/nba/player-analysis/<player_name>', 'required', 'not-applicable', 'json', 'nba'),
    'bet.nba_stat_analysis': _policy('GET', '/nba/stat-analysis', 'required', 'not-applicable', 'html', 'nba'),
}
