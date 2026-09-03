"""Deliberate public-page, metadata, and breadcrumb contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicPage:
    endpoint: str
    path: str
    title: str
    description: str
    kicker: str
    intro: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    last_modified: str = '2026-09-03'


HOME_PAGE = PublicPage(
    endpoint='main.home',
    path='/',
    title='NBA bet tracking and projection analysis',
    description=(
        'Track NBA bets, review performance, and compare informational '
        'player-prop projections in one private ledger.'
    ),
    kicker='NBA analytics · Private ledger',
    intro=(
        'A focused workspace for recording wagers, measuring results, and '
        'reviewing model-assisted NBA prop analysis.'
    ),
    sections=(),
)


PUBLIC_PAGES = (
    PublicPage(
        endpoint='main.methodology',
        path='/methodology',
        title='Model methodology and limitations',
        description=(
            'How Edge Tracker builds NBA projections, evaluates signals, and '
            'limits claims when market validation is incomplete.'
        ),
        kicker='Trust center · Methodology',
        intro=(
            'Edge Tracker separates statistical projections from betting '
            'outcomes. A model estimate is evidence to inspect, not a promise.'
        ),
        sections=(
            ('What the models do', (
                'XGBoost models estimate selected NBA player statistics from historical game logs and a fixed feature contract.',
                'A separate quality layer can compare a projection with an available market line and report uncertainty or a confidence tier.',
            )),
            ('Validation standard', (
                'Training and inference share one feature builder, and evaluation uses time-aware splits to reduce leakage from future games.',
                'Profitability remains unproven until enough real, time-stamped market quotes and graded outcomes pass the documented validation gates.',
            )),
            ('Known limits', (
                'Injuries, late lineup changes, minutes restrictions, provider gaps, and market movement can make a projection stale or incomplete.',
                'Historical fit does not guarantee future accuracy. Always inspect the source time, line, price, and uncertainty before acting.',
            )),
        ),
    ),
    PublicPage(
        endpoint='main.responsible_gambling',
        path='/responsible-gambling',
        title='Responsible gambling',
        description=(
            'Practical limits and support guidance for using Edge Tracker '
            'without treating projections as guaranteed outcomes.'
        ),
        kicker='Trust center · Player safety',
        intro=(
            'Betting involves financial risk. Edge Tracker is an analysis and '
            'record-keeping tool; it cannot make gambling safe or profitable.'
        ),
        sections=(
            ('Set hard limits', (
                'Use only money you can afford to lose, define deposit and time limits in advance, and never chase a loss.',
                'Do not borrow to gamble. Stop when betting affects sleep, work, relationships, or essential spending.',
            )),
            ('Eligibility and location', (
                'Use gambling products only if you meet the legal age and jurisdiction requirements where you are located.',
                'Edge Tracker does not accept wagers and does not determine whether a sportsbook or wager is legal in your location.',
            )),
            ('Get support', (
                'If gambling feels difficult to control, pause immediately and use the self-exclusion and support resources offered by your local regulator or licensed operator.',
                'For an immediate mental-health or safety emergency, contact local emergency services.',
            )),
        ),
    ),
    PublicPage(
        endpoint='main.privacy',
        path='/privacy',
        title='Privacy notice',
        description='What account and betting data Edge Tracker stores and how it is used.',
        kicker='Trust center · Privacy',
        intro=(
            'Edge Tracker stores the information needed to operate your '
            'account and private betting ledger. It is not sold for advertising.'
        ),
        sections=(
            ('Data you provide', (
                'Account records include a username, email address, and a one-way password hash. The application never stores your plain-text password.',
                'Bet records can include teams, players, market details, odds, stake, notes, and results that you choose to enter.',
            )),
            ('Operational data', (
                'The service may log request identifiers, timing, errors, and a small allowlist of product events used to diagnose reliability and usability.',
                'Sensitive form values and free-text betting notes are excluded from the product-event allowlist.',
            )),
            ('Control and retention', (
                'You can export your bet history from the application. Account deletion and formal retention handling must be completed before a hosted public launch.',
                'Production hosting providers may process data as infrastructure operators; their final identities and retention terms depend on the deployed environment.',
            )),
        ),
    ),
    PublicPage(
        endpoint='main.terms',
        path='/terms',
        title='Terms of use',
        description='The basic conditions for using Edge Tracker and its informational projections.',
        kicker='Trust center · Terms',
        intro=(
            'These plain-language terms describe the current project baseline. '
            'Jurisdiction-specific legal review is required before public launch.'
        ),
        sections=(
            ('Informational use only', (
                'Projections, confidence labels, and recommendations are informational estimates, not financial advice, guarantees, or instructions to place a wager.',
                'You remain responsible for checking prices, rules, legality, and suitability before making any betting decision.',
            )),
            ('Acceptable use', (
                'Do not attempt to bypass access controls, disrupt the service, scrape private data, or use another person’s account.',
                'Third-party sports and odds data remains subject to the rights and terms of its provider.',
            )),
            ('Availability and risk', (
                'The service may be changed, suspended, or unavailable, and data may be delayed or incorrect.',
                'To the extent permitted by applicable law, you assume the risk of decisions made using the service.',
            )),
        ),
    ),
    PublicPage(
        endpoint='main.data_sources',
        path='/data-sources',
        title='Data sources and attribution',
        description='The sports, odds, and user-entered data used by Edge Tracker.',
        kicker='Trust center · Data provenance',
        intro=(
            'Every useful projection depends on data provenance. Edge Tracker '
            'keeps provider inputs separate from its own derived analysis.'
        ),
        sections=(
            ('Sports data', (
                'NBA schedules, scores, box scores, and player logs are collected from configured basketball data providers, including ESPN endpoints where enabled.',
                'Provider availability and field formats can change. Cached snapshots record when data was fetched so stale inputs can be identified.',
            )),
            ('Market data', (
                'Current and historical odds can be supplied by The Odds API when an operator configures a valid API key and compatible subscription.',
                'Book prices move. A displayed line is not guaranteed to remain available, and missing credentials cause the related views to degrade rather than fabricate data.',
            )),
            ('Derived and user data', (
                'Projections, edges, confidence labels, and trend summaries are Edge Tracker calculations derived from available inputs.',
                'Bet history is entered or confirmed by the account holder and remains separate from provider data.',
            )),
        ),
    ),
    PublicPage(
        endpoint='main.about',
        path='/about',
        title='About and contact',
        description='About the Edge Tracker project and how to report a problem.',
        kicker='Project information',
        intro=(
            'Edge Tracker is an independently developed NBA analysis and bet '
            'tracking project focused on transparent records and cautious models.'
        ),
        sections=(
            ('Project scope', (
                'The current product covers an authenticated NBA workflow: manual bet tracking, results, projections, prop analysis, and operational health checks.',
                'It is not a sportsbook, does not take custody of funds, and does not place wagers on a user’s behalf.',
            )),
            ('Contact', (
                'Use the project’s GitHub issue tracker to report a reproducible bug or documentation problem without including account, credential, or private betting information.',
                'Security reports should avoid public disclosure until a private reporting channel is configured for hosted operation.',
            )),
        ),
    ),
)

PUBLIC_PAGE_BY_ENDPOINT = {
    page.endpoint: page for page in (HOME_PAGE, *PUBLIC_PAGES)
}

PUBLIC_ENDPOINTS = frozenset(PUBLIC_PAGE_BY_ENDPOINT)


BREADCRUMB_LABELS = {
    'auth.login': 'Sign in',
    'auth.register': 'Create account',
    'main.dashboard': 'Dashboard',
    'bet.place_bet': 'My bets',
    'bet.new_bet': 'Add bet',
    'bet.nba_today': 'NBA today',
    'bet.nba_analysis': 'Prop analysis',
    'bet.nba_stat_analysis': 'Stat analysis',
}


def breadcrumbs_for(endpoint: str | None) -> list[dict[str, object]]:
    """Return a small shared breadcrumb view model for a rendered endpoint."""
    if not endpoint or endpoint == 'main.home':
        return []
    public_page = PUBLIC_PAGE_BY_ENDPOINT.get(endpoint)
    label = public_page.title if public_page else BREADCRUMB_LABELS.get(endpoint)
    if not label:
        return []
    return [
        {'label': 'Home', 'url': '/', 'current': False},
        {'label': label, 'url': None, 'current': True},
    ]
