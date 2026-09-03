"""Canonical server-side prop-market mappings and template labels."""

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

# Long labels used by browser-side market selectors and bet slips.
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


INDICATOR_TIERS: dict[str, dict[str, str]] = {
    "strong": {
        "label": "STRONG",
        "client_class": "tier-strong",
        "badge_class": "sa-ind-badge-strong",
    },
    "value": {
        "label": "VALUE",
        "client_class": "tier-moderate",
        "badge_class": "sa-ind-badge-value",
    },
    "slight": {
        "label": "SLIGHT",
        "client_class": "tier-slight",
        "badge_class": "sa-ind-badge-slight",
    },
    "avoid": {
        "label": "AVOID",
        "client_class": "sa-badge-avoid",
        "badge_class": "sa-ind-badge-avoid",
    },
}

POS_EDGE_APPLICABLE_PROPS: tuple[str, ...] = ("player_points",)


# ---------------------------------------------------------------------------
# Helpers for injecting display config into Jinja context
# ---------------------------------------------------------------------------

def get_client_display_config() -> dict:
    """Build the browser display contract from the canonical Python mappings."""
    return {
        "market_labels": PROP_LABELS_LONG,
        "market_labels_short": PROP_LABELS_SHORT,
        "prop_to_stat_col": PROP_STAT_KEY,
        "indicator_classes": {
            key: value["client_class"] for key, value in INDICATOR_TIERS.items()
        },
        "indicator_labels": {
            key: value["label"] for key, value in INDICATOR_TIERS.items()
        },
        "pos_edge_applicable_props": list(POS_EDGE_APPLICABLE_PROPS),
    }


def get_template_display_config() -> dict:
    """Return canonical display data for Jinja and browser bootstrap code."""
    return {
        "PROP_LABELS_SHORT": PROP_LABELS_SHORT,
        "INDICATOR_TIERS": INDICATOR_TIERS,
        "CLIENT_DISPLAY_CONFIG": get_client_display_config(),
    }
