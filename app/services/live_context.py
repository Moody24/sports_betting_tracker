"""Pre-game scenario context for live prop scoring.

Builds the dim->bucket dict that agreement_score matches against
ScenarioSplit buckets. Emits ONLY dimensions knowable before tip-off, with
bucket labels byte-identical to what refresh_splits stored (fixed logic is
shared via scenario_dimensions helpers; quantile-dependent buckets come from
the persisted ScenarioContextPack). game_script (realized margin) and
teammate_context (needs injury data) are never emitted.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

from app.models import HistoricalGameLog, ScenarioContextPack
from app.services.espn_mapping import normalize_abbr
from app.services.scenario_dimensions import (
    fav_dog_label,
    rest_bucket_label,
    season_segment_label,
)
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

MAX_PACK_AGE_DAYS = 7
ROLE_LOOKBACK = 5
ROLE_STARTER_MIN = 3


def get_live_pack(sport: str = 'nba') -> tuple:
    """Return ``(payload_dict | None, fresh: bool)`` for the sport's pack."""
    row = ScenarioContextPack.query.filter_by(sport=sport).first()
    if row is None:
        return None, False
    computed = row.computed_at
    if computed.tzinfo is None:
        computed = computed.replace(tzinfo=timezone.utc)
    fresh = (datetime.now(timezone.utc) - computed) <= timedelta(
        days=MAX_PACK_AGE_DAYS)
    try:
        return json.loads(row.payload), fresh
    except ValueError:
        logger.warning("live_context: unreadable pack payload for %s", sport)
        return None, False


def _bucket_from_bins(value: float, bins,
                      labels: tuple = ('low', 'mid', 'high')):
    if not bins or len(bins) != 4:
        return None
    if value <= bins[1]:
        return labels[0]
    if value <= bins[2]:
        return labels[1]
    return labels[2]


def build_live_context(espn_id: str, *, team_abbr: str, opponent_abbr: str,
                       is_home: bool, game_date: date | None = None,
                       total: float | None = None,
                       spread: float | None = None,
                       favored_side: str | None = None,
                       sport: str = 'nba',
                       pack: tuple | None = None) -> tuple:
    """Return ``(context_dict, pack_fresh)`` for one (player, game).

    ``pack`` (optional) is a prefetched ``get_live_pack()`` result; when
    provided the pack query is skipped — callers scoring many props in one
    scan should fetch the pack once and pass it here."""
    as_of = game_date or datetime.now(ET).date()
    ctx: dict = {'home_away': 'home' if is_home else 'away'}

    segment = season_segment_label(as_of)
    if segment is not None:
        ctx['season_segment'] = segment

    recent = (HistoricalGameLog.query
              .filter(HistoricalGameLog.sport == sport,
                      HistoricalGameLog.player_id == str(espn_id),
                      HistoricalGameLog.game_date < as_of)
              .order_by(HistoricalGameLog.game_date.desc())
              .limit(ROLE_LOOKBACK).all())
    if recent:
        days_rest = (as_of - recent[0].game_date).days - 1
        ctx['rest_bucket'] = rest_bucket_label(days_rest)
        flags = [r.starter for r in recent if r.starter is not None]
        if flags:
            started = sum(1 for f in flags if f)
            ctx['role'] = ('starter' if started >= ROLE_STARTER_MIN
                           else 'bench')
    else:
        ctx['rest_bucket'] = rest_bucket_label(99)   # first-game convention

    if spread is not None and favored_side in ('home', 'away'):
        team_is_favored = (favored_side == 'home') == is_home
        ctx['fav_dog'] = fav_dog_label(float(spread), team_is_favored)

    pack, fresh = get_live_pack(sport) if pack is None else pack
    if pack:
        opp = normalize_abbr((opponent_abbr or '').strip().upper())
        team = normalize_abbr((team_abbr or '').strip().upper())
        tier = pack.get('team_def_tier', {}).get(opp)
        if tier:
            ctx['opp_def_tier'] = tier
        poss = pack.get('team_game_poss', {})
        if team in poss and opp in poss:
            est = (poss[team] + poss[opp]) / 2.0
            pace = _bucket_from_bins(est, pack.get('pace_bins'),
                                     ('slow', 'mid', 'fast'))
            if pace:
                ctx['pace_tier'] = pace
        if total is not None:
            bucket = _bucket_from_bins(float(total), pack.get('total_bins'))
            if bucket:
                ctx['total_bucket'] = bucket
    return ctx, fresh
