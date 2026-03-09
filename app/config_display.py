"""Centralized display configuration for prop labels, badge styles, and UI constants.

This is the single source of truth for all display-related mappings used
across Python templates, Jinja macros, and JavaScript.  Individual pages
should import from here rather than defining their own inline dicts.
"""

# ---------------------------------------------------------------------------
# Prop / Market labels
# ---------------------------------------------------------------------------

# Short labels used in badges, table headers, and compact displays
PROP_LABELS_SHORT: dict[str, str] = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PM",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_points_rebounds_assists": "PTS+REB+AST",
    "player_points_rebounds": "PTS+REB",
    "player_points_assists": "PTS+AST",
    "player_rebounds_assists": "REB+AST",
}

# Long labels used in dropdowns, full-text displays
PROP_LABELS_LONG: dict[str, str] = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "3-Pointers",
    "player_blocks": "Blocks",
    "player_steals": "Steals",
    "player_points_rebounds_assists": "Points + Rebounds + Assists",
    "player_points_rebounds": "PTS+REB",
    "player_points_assists": "PTS+AST",
    "player_rebounds_assists": "REB+AST",
}

# prop_type -> internal stat column on PlayerGameLog
PROP_STAT_KEY: dict[str, str] = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
    "player_steals": "stl",
    "player_blocks": "blk",
}

# prop_type -> ESPN boxscore column header
PROP_ESPN_COLUMN: dict[str, str] = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PT",
    "player_blocks": "BLK",
    "player_steals": "STL",
}

# Supported single-stat prop markets (order matters for UI iteration)
SUPPORTED_PROP_MARKETS: list[str] = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_points_rebounds_assists",
    "player_threes",
    "player_steals",
    "player_blocks",
]


def prop_label_short(prop_type: str) -> str:
    """Return short display label for a prop type, with fallback."""
    if not prop_type:
        return "Stat"
    return PROP_LABELS_SHORT.get(
        prop_type,
        prop_type.replace("player_", "").replace("_", " ").upper(),
    )


def prop_label_long(prop_type: str) -> str:
    """Return long display label for a prop type, with fallback."""
    if not prop_type:
        return "Stat"
    return PROP_LABELS_LONG.get(
        prop_type,
        prop_type.replace("player_", "").replace("_", " ").title(),
    )


# ---------------------------------------------------------------------------
# stat_key -> opponent defense allowed field on TeamDefenseSnapshot
# ---------------------------------------------------------------------------

STAT_KEY_TO_OPP_ALLOWED: dict[str, str] = {
    "pts": "opp_pts_pg",
    "reb": "opp_reb_pg",
    "ast": "opp_ast_pg",
    "fg3m": "opp_3pm_pg",
    "stl": "opp_stl_pg",
    "blk": "opp_blk_pg",
}

# prop_type -> opponent defense allowed field (convenience alias)
PROP_TO_OPP_ALLOWED: dict[str, str] = {
    "player_points": "opp_pts_pg",
    "player_rebounds": "opp_reb_pg",
    "player_assists": "opp_ast_pg",
    "player_threes": "opp_3pm_pg",
    "player_steals": "opp_stl_pg",
    "player_blocks": "opp_blk_pg",
}


# ---------------------------------------------------------------------------
# Confidence / indicator tier display
# ---------------------------------------------------------------------------

CONFIDENCE_TIERS: dict[str, dict] = {
    "strong":   {"label": "STRONG", "css_class": "tier-strong",   "badge_class": "text-bg-success"},
    "moderate": {"label": "MODERATE", "css_class": "tier-moderate", "badge_class": "text-bg-warning text-dark"},
    "slight":   {"label": "SLIGHT", "css_class": "tier-slight",   "badge_class": "text-bg-secondary"},
    "no_edge":  {"label": "NO EDGE", "css_class": "tier-slight",  "badge_class": "text-bg-secondary"},
}

# Stat analysis indicator tiers (different vocabulary from confidence tiers)
INDICATOR_TIERS: dict[str, dict] = {
    "strong": {"label": "STRONG", "css_class": "tier-strong",     "badge_class": "sa-ind-badge-strong"},
    "value":  {"label": "VALUE",  "css_class": "tier-moderate",   "badge_class": "sa-ind-badge-value"},
    "slight": {"label": "SLIGHT", "css_class": "tier-slight",     "badge_class": "sa-ind-badge-slight"},
    "avoid":  {"label": "AVOID",  "css_class": "sa-badge-avoid",  "badge_class": "sa-ind-badge-avoid"},
}


# ---------------------------------------------------------------------------
# Outcome / status display
# ---------------------------------------------------------------------------

OUTCOME_DISPLAY: dict[str, dict] = {
    "win":     {"label": "Win",     "css_class": "text-bg-success"},
    "lose":    {"label": "Loss",    "css_class": "text-bg-danger"},
    "push":    {"label": "Push",    "css_class": "text-bg-warning text-dark"},
    "pending": {"label": "Pending", "css_class": "text-bg-secondary"},
}


# ---------------------------------------------------------------------------
# Button semantic hierarchy (documentation / reference)
# ---------------------------------------------------------------------------
# Primary CTA:      btn-primary (solid blue)
# Secondary CTA:    btn-outline-secondary (neutral outline)
# Success/confirm:  btn-success (green) — positive confirmed states
# Warning/manual:   btn-warning text-dark (amber) — manual intervention
# Destructive:      btn-outline-danger (red outline) — delete, remove
# Info/navigation:  btn-outline-info (cyan outline) — refresh, view
# Quick-add bet:    btn-outline-success (green outline) — add to bets
# Quick-add parlay: btn-outline-info (cyan outline) — add to parlay


# ---------------------------------------------------------------------------
# Player position display
# ---------------------------------------------------------------------------

# Player positions are INFERRED from stat profile heuristics, NOT from
# authoritative roster data.  UI should label accordingly.
POSITION_IS_ESTIMATED = True
POSITION_LABEL_PREFIX = "Est."  # e.g., "Est. PG" instead of just "PG"

# Position matchup (Pos Edge) is only meaningful for player_points props.
# Do not display Pos Edge for other stat types.
POS_EDGE_APPLICABLE_PROPS: set[str] = {"player_points"}


# ---------------------------------------------------------------------------
# Helpers for injecting display config into Jinja context
# ---------------------------------------------------------------------------

def get_template_display_config() -> dict:
    """Return a dict of display constants suitable for Jinja template context."""
    return {
        "PROP_LABELS_SHORT": PROP_LABELS_SHORT,
        "PROP_LABELS_LONG": PROP_LABELS_LONG,
        "CONFIDENCE_TIERS": CONFIDENCE_TIERS,
        "INDICATOR_TIERS": INDICATOR_TIERS,
        "OUTCOME_DISPLAY": OUTCOME_DISPLAY,
        "POSITION_IS_ESTIMATED": POSITION_IS_ESTIMATED,
        "POSITION_LABEL_PREFIX": POSITION_LABEL_PREFIX,
        "POS_EDGE_APPLICABLE_PROPS": POS_EDGE_APPLICABLE_PROPS,
        "SUPPORTED_PROP_MARKETS": SUPPORTED_PROP_MARKETS,
    }
