"""Shared context-building helpers for NBA analysis views."""

from app.config_display import PROP_TO_OPP_ALLOWED
from app.models import TeamDefenseSnapshot

POSITION_ORDER = {'PG': 0, 'SG': 1, 'SF': 2, 'PF': 3, 'C': 4}


def build_stat_context(
    score: dict,
    games_today,
    def_snap_map: dict | None = None,
) -> dict:
    """Build defensive and game context for a scored player prop."""
    if isinstance(games_today, dict):
        game = games_today.get(score.get('game_id'), {})
    else:
        game = next(
            (game for game in games_today
             if game.get('espn_id') == score.get('game_id')),
            {},
        )

    ctx = {
        'over_under_line': game.get('over_under_line'),
        'moneyline_home': game.get('moneyline_home'),
        'moneyline_away': game.get('moneyline_away'),
    }
    moneyline_home = game.get('moneyline_home') or 0
    moneyline_away = game.get('moneyline_away') or 0
    ctx['blowout_risk'] = abs(moneyline_home) >= 400 or abs(moneyline_away) >= 400

    player_team = score.get('player_team_abbr') or ''
    home_abbr = (game.get('home') or {}).get('abbr', '')
    away_abbr = (game.get('away') or {}).get('abbr', '')
    opponent_abbr = away_abbr if player_team == home_abbr else home_abbr
    ctx['opp_abbr'] = opponent_abbr

    if def_snap_map is not None:
        defense = def_snap_map.get(opponent_abbr) if opponent_abbr else None
    else:
        defense = (
            TeamDefenseSnapshot.query
            .filter_by(team_abbr=opponent_abbr)
            .order_by(TeamDefenseSnapshot.fetched_at.desc())
            .first()
            if opponent_abbr else None
        )

    if defense:
        ctx['opp_def_rating'] = defense.def_rating
        ctx['opp_pace'] = defense.pace
        opponent_field = PROP_TO_OPP_ALLOWED.get(score.get('prop_type', ''))
        ctx['opp_stat_allowed'] = (
            getattr(defense, opponent_field, None) if opponent_field else None
        )
        position = (score.get('breakdown') or {}).get('player_position', '')
        position_allowed = {
            'PG': defense.opp_pts_allowed_pg,
            'SG': defense.opp_pts_allowed_sg,
            'SF': defense.opp_pts_allowed_sf,
            'PF': defense.opp_pts_allowed_pf,
            'C': defense.opp_pts_allowed_c,
        }
        ctx['opp_pos_allowed'] = position_allowed.get(position)
        ctx['player_position'] = position
    else:
        ctx.update(
            opp_def_rating=None,
            opp_pace=None,
            opp_stat_allowed=None,
            opp_pos_allowed=None,
            player_position='',
        )
    return ctx
