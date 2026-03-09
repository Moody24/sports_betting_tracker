/**
 * Centralized display config for client-side JS.
 * Single source of truth — mirrors app/config_display.py.
 */

var MARKET_LABELS = {
  player_points: 'Points',
  player_rebounds: 'Rebounds',
  player_assists: 'Assists',
  player_threes: '3-Pointers',
  player_blocks: 'Blocks',
  player_steals: 'Steals',
  player_points_rebounds_assists: 'Points + Rebounds + Assists',
  player_points_rebounds: 'PTS+REB',
  player_points_assists: 'PTS+AST',
  player_rebounds_assists: 'REB+AST',
};

var MARKET_LABELS_SHORT = {
  player_points: 'PTS',
  player_rebounds: 'REB',
  player_assists: 'AST',
  player_threes: '3PM',
  player_blocks: 'BLK',
  player_steals: 'STL',
  player_points_rebounds_assists: 'PTS+REB+AST',
  player_points_rebounds: 'PTS+REB',
  player_points_assists: 'PTS+AST',
  player_rebounds_assists: 'REB+AST',
};

// Maps prop_type -> internal stat column key
var PROP_TO_STAT_COL = {
  player_points: 'pts',
  player_rebounds: 'reb',
  player_assists: 'ast',
  player_threes: 'fg3m',
  player_steals: 'stl',
  player_blocks: 'blk',
};

// Confidence / indicator tier styling
var TIER_CLASSES = {
  strong: 'tier-strong',
  moderate: 'tier-moderate',
  slight: 'tier-slight',
  no_edge: 'tier-slight',
};

var INDICATOR_CLASSES = {
  strong: 'tier-strong',
  value: 'tier-moderate',
  slight: 'tier-slight',
  avoid: 'sa-badge-avoid',
};

var INDICATOR_LABELS = {
  strong: 'STRONG',
  value: 'VALUE',
  slight: 'SLIGHT',
  avoid: 'AVOID',
};

// Position edge is only meaningful for player_points
var POS_EDGE_APPLICABLE_PROPS = ['player_points'];
