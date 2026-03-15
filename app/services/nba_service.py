import logging
import os
import time as _time
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from app.config_display import PROP_ESPN_COLUMN, SUPPORTED_PROP_MARKETS
from app.enums import BetType, Outcome
from app.services.base import SportService, SPORT_REGISTRY

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo("America/New_York")

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
)
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
ODDS_API_EVENTS_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/events/"

_STATUS_FINAL = "STATUS_FINAL"

# Module-level caches for expensive API calls.
# get_todays_games: 60s TTL (live scores update frequently)
# fetch_upcoming_games: 5 min TTL (tomorrow's schedule rarely changes)
_GAMES_CACHE: dict = {}
_GAMES_CACHE_TTL = 60
_UPCOMING_CACHE: dict = {}
_UPCOMING_CACHE_TTL = 300


def _et_date_str() -> str:
    return datetime.now(APP_TIMEZONE).strftime("%Y-%m-%d")


# Re-export from centralized config for backward compatibility
PLAYER_PROP_MARKETS = SUPPORTED_PROP_MARKETS
_PROP_STAT_COLUMN = PROP_ESPN_COLUMN


def _get_odds_api_key() -> str:
    return os.getenv("ODDS_API_KEY", "")


def _sanitize_api_error(exc: Exception) -> str:
    """Strip the Odds API key from error messages to prevent log leakage."""
    msg = str(exc)
    api_key = _get_odds_api_key()
    if api_key and api_key in msg:
        msg = msg.replace(api_key, "***REDACTED***")
    return msg


# ── ESPN: live scores ────────────────────────────────────────────────


def fetch_espn_scoreboard(date_str: Optional[str] = None) -> list[dict]:
    """Return NBA games from the ESPN scoreboard endpoint.

    Pass date_str as 'YYYYMMDD' to fetch a specific date; omit for today.
    """
    params = {}
    if date_str:
        params["dates"] = date_str

    try:
        resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("ESPN scoreboard fetch failed: %s", exc)
        return []

    games = []
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])

        home = away = None
        for team in competitors:
            info = {
                "name": team.get("team", {}).get("displayName", ""),
                "abbr": team.get("team", {}).get("abbreviation", ""),
                "score": int(team.get("score", 0) or 0),
                "logo": team.get("team", {}).get("logo", ""),
            }
            if team.get("homeAway") == "home":
                home = info
            else:
                away = info

        if not home or not away:
            continue

        status_obj = event.get("status", {})
        status_type = status_obj.get("type", {})

        games.append(
            {
                "espn_id": event.get("id", ""),
                "name": event.get("name", ""),
                "home": home,
                "away": away,
                "total_score": home["score"] + away["score"],
                "status": status_type.get("name", ""),
                "status_detail": status_type.get("detail", "")
                or status_type.get("description", ""),
                "clock": status_obj.get("displayClock", ""),
                "period": status_obj.get("period", 0),
                "start_time": event.get("date", ""),
            }
        )

    return games


def fetch_espn_boxscore(espn_id: str) -> dict:
    """Fetch final player stats for a completed game.

    Returns a dict keyed by player display name, each value being a dict of
    {prop_type: stat_value} e.g. {"LeBron James": {"player_points": 28, ...}}.
    """
    try:
        resp = requests.get(ESPN_SUMMARY_URL, params={"event": espn_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("ESPN summary fetch failed for event %s: %s", espn_id, exc)
        return {}

    player_stats: dict = {}

    for team_block in data.get("boxscore", {}).get("players", []):
        for stat_block in team_block.get("statistics", []):
            column_names: list[str] = stat_block.get("names", [])
            for athlete in stat_block.get("athletes", []):
                name = athlete.get("athlete", {}).get("displayName", "")
                if not name:
                    continue
                raw_stats: list[str] = athlete.get("stats", [])
                entry: dict = {}
                for prop_type, col_header in _PROP_STAT_COLUMN.items():
                    if col_header not in column_names:
                        continue
                    idx = column_names.index(col_header)
                    if idx >= len(raw_stats):
                        continue
                    raw = raw_stats[idx]
                    # "3PT" comes as "M-A"; take made count
                    if "-" in str(raw):
                        try:
                            raw = raw.split("-")[0]
                        except Exception:
                            continue
                    try:
                        entry[prop_type] = float(raw)
                    except (ValueError, TypeError):
                        pass
                if entry:
                    # Compute PRA so player_points_rebounds_assists bets can be graded
                    pts = entry.get('player_points')
                    reb = entry.get('player_rebounds')
                    ast = entry.get('player_assists')
                    if pts is not None and reb is not None and ast is not None:
                        entry['player_points_rebounds_assists'] = pts + reb + ast
                    player_stats[name] = entry

    return player_stats


# ── The Odds API: over/under lines ──────────────────────────────────


def fetch_odds() -> dict:
    """Return over/under lines from The Odds API (needs ODDS_API_KEY env var)."""
    totals, _ = fetch_odds_combined()
    return totals


def fetch_odds_combined() -> tuple:
    """Return (totals_map, h2h_map) from a single Odds API request.

    totals_map: {matchup_key -> over_under_line (float)}
    h2h_map:    {matchup_key -> {"home": int, "away": int}}
    """
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – odds unavailable")
        return {}, {}

    try:
        resp = requests.get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals,h2h",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (combined) fetch failed: %s", _sanitize_api_error(exc))
        return {}, {}

    totals_map: dict = {}
    h2h_map: dict = {}

    for game in data:
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        key = _matchup_key(home_team, away_team)

        ou_line = None
        home_ml = away_ml = None

        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                mkey = market.get("key", "")

                if mkey == "totals" and ou_line is None:
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point"):
                            ou_line = float(outcome["point"])
                            break

                if mkey == "h2h" and home_ml is None:
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price")
                        if name == home_team:
                            home_ml = price
                        elif name == away_team:
                            away_ml = price

            if ou_line is not None and home_ml is not None:
                break

        if ou_line is not None:
            totals_map[key] = ou_line
        if home_ml is not None or away_ml is not None:
            h2h_map[key] = {"home": home_ml, "away": away_ml}

    return totals_map, h2h_map


# ── Merge scores + odds ─────────────────────────────────────────────


def _matchup_key(team_a: str, team_b: str) -> tuple:
    """Normalised key for matching ESPN names with Odds API names."""
    return tuple(sorted([_normalize_team_name(team_a), _normalize_team_name(team_b)]))


def _normalize_team_name(name: str) -> str:
    """Normalize team names across ESPN/Odds API variants."""
    if not name:
        return ""
    norm = " ".join(str(name).lower().strip().split())
    aliases = {
        "la clippers": "los angeles clippers",
        "la lakers": "los angeles lakers",
        "ny knicks": "new york knicks",
        "okc thunder": "oklahoma city thunder",
        "gs warriors": "golden state warriors",
        "no pelicans": "new orleans pelicans",
    }
    return aliases.get(norm, norm)


def _coerce_match_date(bet) -> Optional[datetime]:
    """Best-effort conversion of a bet's match_date into a datetime."""
    match_dt = getattr(bet, "match_date", None)
    if isinstance(match_dt, datetime):
        return match_dt
    if isinstance(match_dt, date_type):
        return datetime.combine(match_dt, datetime.min.time())
    return None


def _choose_game_for_bet(bet, scoreboards: list[dict], espn_lookup: dict) -> Optional[dict]:
    """Resolve a scoreboard game for a bet, with fallback when game_id is missing."""
    if getattr(bet, "external_game_id", None):
        return espn_lookup.get(bet.external_game_id)

    team_a = getattr(bet, "team_a", "") or ""
    team_b = getattr(bet, "team_b", "") or ""
    if not team_a or not team_b:
        return None

    matchup_key = _matchup_key(team_a, team_b)
    candidates = []
    for game in scoreboards:
        home_name = game.get("home", {}).get("name", "")
        away_name = game.get("away", {}).get("name", "")
        if _matchup_key(home_name, away_name) == matchup_key:
            candidates.append(game)

    if not candidates:
        return None

    match_dt = _coerce_match_date(bet)
    if match_dt:
        # Prefer games closest to the bet's intended date when duplicates exist.
        def _distance_days(game):
            start_raw = game.get("start_time", "")
            try:
                start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
                return abs((start_dt.date() - match_dt.date()).days)
            except Exception:
                return 99

        candidates.sort(key=_distance_days)

    return candidates[0]


def backfill_game_ids(pending_bets: list) -> int:
    """Backfill external_game_id for pending player-prop bets missing one.

    Fetches today's ESPN scoreboard once and matches bets by team names.
    Returns the number of bets updated.
    """
    from app import db

    needs_backfill = [
        b for b in pending_bets
        if b.is_player_prop
        and not b.external_game_id
        and getattr(b, "team_a", None)
        and getattr(b, "team_b", None)
    ]
    if not needs_backfill:
        return 0

    now_et = datetime.now(APP_TIMEZONE)
    date_keys = set()
    for b in needs_backfill:
        md = _coerce_match_date(b)
        if md:
            date_keys.add(md.strftime("%Y%m%d"))
    if not date_keys:
        date_keys.add(now_et.strftime("%Y%m%d"))

    scoreboards: list[dict] = []
    for dk in sorted(date_keys):
        scoreboards.extend(fetch_espn_scoreboard(date_str=dk))

    espn_lookup = {g["espn_id"]: g for g in scoreboards}

    updated = 0
    for b in needs_backfill:
        game = _choose_game_for_bet(b, scoreboards, espn_lookup)
        if game:
            b.external_game_id = game["espn_id"]
            updated += 1

    if updated:
        try:
            db.session.commit()
            logger.info("Backfilled external_game_id for %d bet(s)", updated)
        except Exception:
            db.session.rollback()
            logger.exception("Failed to commit game-id backfill")
            updated = 0

    return updated


def backfill_game_snapshots(
    start_date: date_type,
    end_date: date_type,
    *,
    include_existing: bool = False,
    sleep_seconds: float = 0.15,
) -> dict:
    """Backfill GameSnapshot rows from ESPN scoreboards and enrich odds from Bet history.

    Historical moneyline/O-U are not available from ESPN scoreboards. This backfill
    fills those fields from matching historical Bet records when possible.
    """
    from app import db
    from app.models import Bet, GameSnapshot

    if end_date < start_date:
        return {'error': 'invalid_date_range'}

    bet_rows = (
        Bet.query
        .filter(Bet.match_date.isnot(None))
        .filter(Bet.team_a.isnot(None))
        .filter(Bet.team_b.isnot(None))
        .all()
    )

    # Match key: (game_date_iso, matchup_key)
    line_by_key: dict = {}
    ml_by_key: dict = {}
    for b in bet_rows:
        try:
            b_date = b.match_date.date() if isinstance(b.match_date, datetime) else b.match_date
        except Exception:
            continue
        if not isinstance(b_date, date_type):
            continue
        key = (b_date.isoformat(), _matchup_key(b.team_a or '', b.team_b or ''))
        if b.bet_type in (BetType.OVER.value, BetType.UNDER.value) and b.over_under_line is not None:
            line_by_key.setdefault(key, float(b.over_under_line))
        if b.bet_type == BetType.MONEYLINE.value and b.american_odds is not None:
            picked = _normalize_team_name((b.picked_team or '').strip())
            if picked:
                slot = ml_by_key.setdefault(key, {})
                slot[picked] = int(b.american_odds)

    created = 0
    updated = 0
    ou_filled = 0
    ml_filled = 0
    scanned_days = 0
    scanned_games = 0

    cur = start_date
    while cur <= end_date:
        scanned_days += 1
        date_key = cur.strftime('%Y%m%d')
        games = fetch_espn_scoreboard(date_key)
        for game in games:
            scanned_games += 1
            espn_id = game.get('espn_id')
            if not espn_id:
                continue

            snap = GameSnapshot.query.filter_by(espn_id=espn_id, game_date=cur).first()
            if snap is None:
                snap = GameSnapshot(
                    espn_id=espn_id,
                    game_date=cur,
                    home_team=game.get('home', {}).get('name', ''),
                    away_team=game.get('away', {}).get('name', ''),
                    home_logo=game.get('home', {}).get('logo', ''),
                    away_logo=game.get('away', {}).get('logo', ''),
                    home_score=game.get('home', {}).get('score'),
                    away_score=game.get('away', {}).get('score'),
                    status=game.get('status') or 'STATUS_SCHEDULED',
                    is_final=(game.get('status') == _STATUS_FINAL),
                )
                db.session.add(snap)
                created += 1
            else:
                if include_existing:
                    snap.home_team = game.get('home', {}).get('name', snap.home_team)
                    snap.away_team = game.get('away', {}).get('name', snap.away_team)
                    snap.home_logo = snap.home_logo or game.get('home', {}).get('logo', '')
                    snap.away_logo = snap.away_logo or game.get('away', {}).get('logo', '')
                    snap.home_score = game.get('home', {}).get('score')
                    snap.away_score = game.get('away', {}).get('score')
                    snap.status = game.get('status') or snap.status
                    if game.get('status') == _STATUS_FINAL:
                        snap.is_final = True
                    updated += 1

            odds_key = (cur.isoformat(), _matchup_key(snap.home_team, snap.away_team))
            if snap.over_under_line is None and odds_key in line_by_key:
                snap.over_under_line = line_by_key[odds_key]
                ou_filled += 1

            if (snap.moneyline_home is None or snap.moneyline_away is None) and odds_key in ml_by_key:
                slot = ml_by_key[odds_key]
                home_norm = _normalize_team_name(snap.home_team)
                away_norm = _normalize_team_name(snap.away_team)
                if snap.moneyline_home is None:
                    snap.moneyline_home = slot.get(home_norm)
                if snap.moneyline_away is None:
                    snap.moneyline_away = slot.get(away_norm)
                if snap.moneyline_home is not None or snap.moneyline_away is not None:
                    ml_filled += 1

        db.session.commit()
        if sleep_seconds > 0:
            _time.sleep(sleep_seconds)
        cur += timedelta(days=1)

    return {
        'scanned_days': scanned_days,
        'scanned_games': scanned_games,
        'created': created,
        'updated': updated,
        'ou_filled': ou_filled,
        'moneyline_filled': ml_filled,
    }


def _extract_market_lines_from_odds_game(game: dict) -> tuple[float | None, int | None, int | None]:
    """Extract (ou_line, home_ml, away_ml) from an odds payload game entry."""
    home_team = game.get("home_team", "")
    away_team = game.get("away_team", "")
    ou_line = None
    home_ml = away_ml = None

    for bookmaker in game.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            mkey = market.get("key", "")

            if mkey == "totals" and ou_line is None:
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") == "Over" and outcome.get("point") is not None:
                        try:
                            ou_line = float(outcome.get("point"))
                        except (TypeError, ValueError):
                            ou_line = None
                        break

            if mkey == "h2h" and (home_ml is None or away_ml is None):
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "")
                    price = outcome.get("price")
                    try:
                        price_int = int(price) if price is not None else None
                    except (TypeError, ValueError):
                        price_int = None
                    if name == home_team and price_int is not None:
                        home_ml = price_int
                    elif name == away_team and price_int is not None:
                        away_ml = price_int

        if ou_line is not None and home_ml is not None and away_ml is not None:
            break

    return ou_line, home_ml, away_ml


def _fetch_historical_odds_for_date(target_date: date_type) -> tuple[list[dict], str]:
    """Fetch historical odds rows for a date using provider historical endpoint."""
    api_key = _get_odds_api_key()
    if not api_key:
        return [], 'missing_api_key'

    url = os.getenv(
        "ODDS_API_HISTORICAL_URL",
        "https://api.the-odds-api.com/v4/historical/sports/basketball_nba/odds",
    )
    snapshot_hour = int(os.getenv("ODDS_API_HISTORICAL_SNAPSHOT_HOUR_UTC", "18"))
    snapshot_hour = max(0, min(snapshot_hour, 23))
    snap_ts = f"{target_date.isoformat()}T{snapshot_hour:02d}:00:00Z"

    try:
        resp = requests.get(
            url,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals,h2h",
                "oddsFormat": "american",
                "date": snap_ts,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Historical odds fetch failed for %s: %s", target_date, _sanitize_api_error(exc))
        return [], 'historical_endpoint_error'

    if isinstance(payload, list):
        return payload, 'ok'
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data, 'ok'
    return [], 'historical_payload_empty'


def _fetch_standard_odds_for_date_window(target_date: date_type) -> tuple[list[dict], str]:
    """Fallback: query standard odds endpoint scoped to a single-date UTC window."""
    api_key = _get_odds_api_key()
    if not api_key:
        return [], 'missing_api_key'

    start_et = datetime.combine(target_date, datetime.min.time(), tzinfo=APP_TIMEZONE)
    end_et = start_et + timedelta(days=1)
    start_utc = start_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = end_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        resp = requests.get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals,h2h",
                "oddsFormat": "american",
                "commenceTimeFrom": start_utc,
                "commenceTimeTo": end_utc,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Standard odds fallback failed for %s: %s", target_date, _sanitize_api_error(exc))
        return [], 'standard_endpoint_error'

    return data if isinstance(data, list) else [], 'ok'


def ingest_historical_market_odds(
    start_date: date_type,
    end_date: date_type,
    *,
    force: bool = False,
    sleep_seconds: float = 0.1,
) -> dict:
    """Enrich historical GameSnapshot rows with market odds from provider APIs."""
    from app import db
    from app.models import GameSnapshot

    if end_date < start_date:
        return {'error': 'invalid_date_range'}

    scanned_days = 0
    odds_games = 0
    matched_snapshots = 0
    ou_updated = 0
    ml_updated = 0
    fallback_days = 0
    errors = 0

    cur = start_date
    while cur <= end_date:
        scanned_days += 1

        games, status = _fetch_historical_odds_for_date(cur)
        if not games:
            games, status2 = _fetch_standard_odds_for_date_window(cur)
            if status2 == 'ok':
                fallback_days += 1
            status = status2 if status2 != 'ok' else status

        if not games:
            if status not in ('missing_api_key', 'historical_payload_empty'):
                errors += 1
            cur += timedelta(days=1)
            if sleep_seconds > 0:
                _time.sleep(sleep_seconds)
            continue

        odds_games += len(games)
        by_matchup: dict[tuple, tuple[float | None, int | None, int | None]] = {}
        for g in games:
            home = g.get("home_team", "")
            away = g.get("away_team", "")
            if not home or not away:
                continue
            key = _matchup_key(home, away)
            ou_line, home_ml, away_ml = _extract_market_lines_from_odds_game(g)
            if key not in by_matchup:
                by_matchup[key] = (ou_line, home_ml, away_ml)
                continue
            # Keep first non-null values encountered.
            prev_ou, prev_h, prev_a = by_matchup[key]
            by_matchup[key] = (
                prev_ou if prev_ou is not None else ou_line,
                prev_h if prev_h is not None else home_ml,
                prev_a if prev_a is not None else away_ml,
            )

        snaps = GameSnapshot.query.filter_by(game_date=cur).all()
        for snap in snaps:
            key = _matchup_key(snap.home_team, snap.away_team)
            if key not in by_matchup:
                continue
            matched_snapshots += 1
            ou_line, home_ml, away_ml = by_matchup[key]

            if ou_line is not None and (force or snap.over_under_line is None):
                if snap.over_under_line != ou_line:
                    snap.over_under_line = ou_line
                    ou_updated += 1

            if home_ml is not None and (force or snap.moneyline_home is None):
                if snap.moneyline_home != home_ml:
                    snap.moneyline_home = home_ml
                    ml_updated += 1

            if away_ml is not None and (force or snap.moneyline_away is None):
                if snap.moneyline_away != away_ml:
                    snap.moneyline_away = away_ml
                    ml_updated += 1

        db.session.commit()
        cur += timedelta(days=1)
        if sleep_seconds > 0:
            _time.sleep(sleep_seconds)

    return {
        'scanned_days': scanned_days,
        'odds_games': odds_games,
        'matched_snapshots': matched_snapshots,
        'ou_updated': ou_updated,
        'moneyline_updated': ml_updated,
        'fallback_days': fallback_days,
        'errors': errors,
    }


def fetch_odds_events() -> dict:
    """Return Odds API events with their IDs, mapped by matchup key."""
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – player props unavailable")
        return {}

    try:
        resp = requests.get(
            ODDS_API_EVENTS_URL,
            params={"apiKey": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (events) fetch failed: %s", _sanitize_api_error(exc))
        return {}

    event_map = {}
    for event in data:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        key = _matchup_key(home, away)
        event_map[key] = event.get("id", "")

    return event_map


def fetch_upcoming_games() -> list[dict]:
    """Return tomorrow's NBA games, preferring Odds API (has O/U lines).

    Falls back to ESPN scoreboard when the Odds API key is missing, quota is
    exhausted, or the API returns an empty list.  Cached for
    _UPCOMING_CACHE_TTL seconds — tomorrow's schedule changes rarely.
    """
    cache_date = _et_date_str()
    cached = _UPCOMING_CACHE.get(cache_date)
    if cached and _time.monotonic() < cached["expires_at"]:
        return list(cached["games"])

    now_et = datetime.now(APP_TIMEZONE)
    tomorrow = now_et.date() + timedelta(days=1)

    games = _fetch_upcoming_games_odds(tomorrow)
    if not games:
        logger.info("Odds API returned no upcoming games — falling back to ESPN scoreboard")
        games = _fetch_upcoming_games_espn(tomorrow)

    _UPCOMING_CACHE.clear()
    _UPCOMING_CACHE[cache_date] = {"games": games, "expires_at": _time.monotonic() + _UPCOMING_CACHE_TTL}
    return games


def _fetch_upcoming_games_odds(tomorrow) -> list[dict]:
    """Fetch tomorrow's games with O/U lines from The Odds API."""
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – skipping Odds API for upcoming games")
        return []

    day_after = tomorrow + timedelta(days=1)
    start_et = datetime.combine(tomorrow, datetime.min.time(), tzinfo=APP_TIMEZONE)
    end_et = datetime.combine(day_after, datetime.min.time(), tzinfo=APP_TIMEZONE)
    start_utc = start_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = end_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        resp = requests.get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals",
                "oddsFormat": "american",
                "commenceTimeFrom": start_utc,
                "commenceTimeTo": end_utc,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (upcoming games) fetch failed: %s", _sanitize_api_error(exc))
        return []

    games = []
    for game in data:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        commence = game.get("commence_time", "")

        line = None
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") == "totals":
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point"):
                            line = float(outcome["point"])
                            break
                if line is not None:
                    break
            if line is not None:
                break

        match_date = tomorrow.isoformat()
        if commence:
            try:
                match_date = datetime.fromisoformat(
                    commence.replace("Z", "+00:00")
                ).date().isoformat()
            except ValueError:
                pass

        games.append({
            "espn_id": game.get("id", ""),
            "home": {"name": home, "score": 0, "logo": "", "abbr": ""},
            "away": {"name": away, "score": 0, "logo": "", "abbr": ""},
            "start_time": commence,
            "match_date": match_date,
            "status": "STATUS_SCHEDULED",
            "status_detail": "Tomorrow",
            "clock": "",
            "period": 0,
            "total_score": 0,
            "over_under_line": line,
            "odds_event_id": game.get("id", ""),
        })

    return games


def _fetch_upcoming_games_espn(tomorrow) -> list[dict]:
    """Fetch tomorrow's games from ESPN scoreboard (no O/U lines, but always available)."""
    date_str = tomorrow.strftime("%Y%m%d")
    espn_games = fetch_espn_scoreboard(date_str)

    games = []
    for game in espn_games:
        # Only include scheduled/pre-game events; skip in-progress and completed games.
        if game.get("status") not in ("STATUS_SCHEDULED", ""):
            continue

        commence = game.get("start_time", "")
        games.append({
            "espn_id": game["espn_id"],
            "home": {
                "name": game["home"]["name"],
                "abbr": game["home"].get("abbr", ""),
                "score": 0,
                "logo": game["home"].get("logo", ""),
            },
            "away": {
                "name": game["away"]["name"],
                "abbr": game["away"].get("abbr", ""),
                "score": 0,
                "logo": game["away"].get("logo", ""),
            },
            "start_time": commence,
            "match_date": tomorrow.isoformat(),
            "status": "STATUS_SCHEDULED",
            "status_detail": "Tomorrow",
            "clock": "",
            "period": 0,
            "total_score": 0,
            "over_under_line": None,
            "odds_event_id": "",
        })

    return games


_TARGET_BOOKMAKERS = ["fanduel", "draftkings"]


def _american_to_decimal(odds_int: int) -> float:
    """Convert American odds to decimal odds for comparison (higher = better)."""
    if odds_int >= 0:
        return 1 + odds_int / 100
    return 1 + 100 / abs(odds_int)


def _best_odds(books_dict: dict, side: str) -> tuple:
    """Return (best_american_odds, book_name) for a given side across all books.

    Higher American odds = better (e.g. -105 beats -115, +115 beats +105).
    """
    best_odds_val: Optional[int] = None
    best_book: str = ""
    best_decimal: float = -1.0
    for book_name, book_data in books_dict.items():
        odds_val = book_data.get(f"{side}_odds")
        if odds_val is None:
            continue
        odds_int = int(odds_val)
        dec = _american_to_decimal(odds_int)
        if dec > best_decimal:
            best_odds_val = odds_int
            best_book = book_name
            best_decimal = dec

    return best_odds_val, best_book


def fetch_player_props_for_event(odds_event_id: str) -> dict:
    """Fetch player prop lines for a specific Odds API event.

    Returns a dict keyed by market name, each containing a list of
    {player, line, over_odds, under_odds, books, best_over_book, best_under_book} dicts.
    The flat over_odds/under_odds are the best available across all bookmakers.
    """
    api_key = _get_odds_api_key()
    if not api_key or not odds_event_id:
        return {}

    url = f"{ODDS_API_EVENTS_URL}{odds_event_id}/odds"
    try:
        resp = requests.get(
            url,
            params={
                "apiKey": api_key,
                "bookmakers": ",".join(_TARGET_BOOKMAKERS),
                "markets": ",".join(PLAYER_PROP_MARKETS),
                "oddsFormat": "american",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (player props) fetch failed for event %s: %s", odds_event_id, _sanitize_api_error(exc))
        return {}

    # Collect per-bookmaker data: {(market, player) -> {book -> {over_odds, under_odds, line}}}
    per_book: dict = {}

    for bookmaker in data.get("bookmakers", []):
        book_name = (bookmaker.get("key") or "").lower()
        for market in bookmaker.get("markets", []):
            market_key = market.get("key", "")
            if market_key not in PLAYER_PROP_MARKETS:
                continue

            outcomes = market.get("outcomes", [])
            player_lines: dict = {}
            for outcome in outcomes:
                player = outcome.get("description", "")
                if not player:
                    continue
                if player not in player_lines:
                    player_lines[player] = {}
                side = outcome.get("name", "").lower()
                player_lines[player][side] = {
                    "odds": outcome.get("price", 0),
                    "point": outcome.get("point"),
                }

            for player, sides in player_lines.items():
                over = sides.get("over", {})
                under = sides.get("under", {})
                line = over.get("point") or under.get("point")
                if line is None:
                    continue

                combo_key = (market_key, player)
                if combo_key not in per_book:
                    per_book[combo_key] = {"line": float(line)}
                per_book[combo_key][book_name] = {
                    "over_odds": int(over.get("odds", 0)) if over.get("odds") is not None else None,
                    "under_odds": int(under.get("odds", 0)) if under.get("odds") is not None else None,
                }

    # Build output
    props: dict = {}
    for (market_key, player), book_data in per_book.items():
        line = book_data.pop("line", None)
        if line is None:
            continue

        books = {k: v for k, v in book_data.items() if k in _TARGET_BOOKMAKERS}

        best_over_odds, best_over_book = _best_odds(books, "over")
        best_under_odds, best_under_book = _best_odds(books, "under")

        props.setdefault(market_key, []).append({
            "player": player,
            "line": float(line),
            "over_odds": best_over_odds if best_over_odds is not None else 0,
            "under_odds": best_under_odds if best_under_odds is not None else 0,
            "books": books,
            "best_over_book": best_over_book,
            "best_under_book": best_under_book,
        })

    # Sort each market by player name
    for market_key in props:
        props[market_key].sort(key=lambda p: p["player"])

    return props


def snapshot_todays_props() -> int:
    """Snapshot today's player props odds into OddsSnapshot for line movement tracking.

    Skips a (game_id, player, market, bookmaker) combo if already snapped within 90 min.
    Returns count of rows inserted.
    """
    from datetime import timedelta
    from app import db
    from app.models import OddsSnapshot

    ET_NOW = datetime.now(APP_TIMEZONE)
    today = ET_NOW.date()
    cutoff = ET_NOW - timedelta(minutes=90)

    games = get_todays_games()
    if not games:
        return 0

    # Pre-load all recent snapshots for today in one query to avoid N+1
    recent_snapped: set = {
        (row.game_id, row.player_name, row.market, row.bookmaker)
        for row in OddsSnapshot.query
        .filter(OddsSnapshot.game_date == today, OddsSnapshot.snapped_at >= cutoff)
        .all()
    }

    inserted = 0
    for game in games:
        event_id = (game.get("odds_event_id") or "").strip()
        if not event_id:
            continue

        props = fetch_player_props_for_event(event_id)
        game_id = game.get("espn_id", "")

        for market_key, market_props in props.items():
            for prop in market_props:
                books = prop.get("books", {})
                for book_name, book_data in books.items():
                    if book_name not in _TARGET_BOOKMAKERS:
                        continue

                    if (game_id, prop["player"], market_key, book_name) in recent_snapped:
                        continue

                    snap = OddsSnapshot(
                        game_id=game_id,
                        game_date=today,
                        player_name=prop["player"],
                        market=market_key,
                        bookmaker=book_name,
                        line=prop["line"],
                        over_odds=book_data.get("over_odds"),
                        under_odds=book_data.get("under_odds"),
                    )
                    db.session.add(snap)
                    inserted += 1

    if inserted:
        db.session.commit()

    logger.info("snapshot_todays_props: inserted %d rows", inserted)
    return inserted


def get_todays_games() -> list[dict]:
    """Combined view: ESPN live scores merged with Odds API lines + moneylines.

    Cached for _GAMES_CACHE_TTL seconds per ET date to avoid repeated API
    calls on every page load.  The scheduler and NBA Today upsert path call
    this frequently, so even a 60-second cache cuts many redundant requests.
    """
    cache_date = _et_date_str()
    cached = _GAMES_CACHE.get(cache_date)
    if cached and _time.monotonic() < cached["expires_at"]:
        return list(cached["games"])

    _t0 = _time.perf_counter()
    today_et = datetime.now(APP_TIMEZONE).strftime("%Y%m%d")

    _t = _time.perf_counter()
    games = fetch_espn_scoreboard(date_str=today_et)
    logger.info("PERF espn_scoreboard: games=%d elapsed=%.2fs", len(games), _time.perf_counter() - _t)

    _t = _time.perf_counter()
    totals, h2h = fetch_odds_combined()
    logger.info("PERF odds_combined: matchups=%d elapsed=%.2fs", len(totals), _time.perf_counter() - _t)

    _t = _time.perf_counter()
    events = fetch_odds_events()
    logger.info("PERF odds_events: events=%d elapsed=%.2fs", len(events), _time.perf_counter() - _t)

    for game in games:
        key = _matchup_key(game["home"]["name"], game["away"]["name"])
        game["over_under_line"] = totals.get(key)
        game["odds_event_id"] = events.get(key, "")
        ml = h2h.get(key, {})
        game["moneyline_home"] = ml.get("home")
        game["moneyline_away"] = ml.get("away")

    logger.info("PERF get_todays_games: total_elapsed=%.2fs", _time.perf_counter() - _t0)
    _GAMES_CACHE.clear()
    _GAMES_CACHE[cache_date] = {"games": games, "expires_at": _time.monotonic() + _GAMES_CACHE_TTL}
    return games


def get_player_props(espn_id: str, games: Optional[list[dict]] = None) -> dict:
    """Get player props for a game identified by ESPN ID.

    Looks up the Odds API event ID via team-name matching, then fetches props.
    """
    if games is None:
        games = get_todays_games()

    for game in games:
        if game["espn_id"] == espn_id:
            return fetch_player_props_for_event(game.get("odds_event_id", ""))

    return {}


# ── Result checker ───────────────────────────────────────────────────


def resolve_pending_bets(pending_bets: list) -> list[tuple]:
    """Check ESPN for final scores and resolve all pending bet types.

    Handles over/under, moneyline, and player prop bets.
    Returns list of (bet, new_outcome, actual_value) for bets that can be graded.
    """
    # Collect scoreboards for relevant bet dates so older pending bets can settle.
    # Fallback to "today" when no usable bet dates are present.
    scoreboards: list[dict] = []
    date_keys: set[str] = set()
    for bet in pending_bets:
        match_dt = getattr(bet, "match_date", None)
        if not match_dt:
            continue
        try:
            match_date = match_dt.date() if isinstance(match_dt, datetime) else match_dt
            for delta_days in (-1, 0, 1):
                day = match_date + timedelta(days=delta_days)
                date_keys.add(day.strftime("%Y%m%d"))
        except Exception:
            continue

    if date_keys:
        for date_key in sorted(date_keys):
            scoreboards.extend(fetch_espn_scoreboard(date_str=date_key))
    else:
        scoreboards = fetch_espn_scoreboard()

    espn_lookup: dict = {}
    for g in scoreboards:
        espn_lookup[g["espn_id"]] = g

    # Cache box scores so we only fetch each game once
    boxscore_cache: dict = {}

    results = []
    for bet in pending_bets:
        game = _choose_game_for_bet(bet, scoreboards, espn_lookup)
        if not game:
            continue
        if game["status"] != _STATUS_FINAL:
            continue

        # ── Over / Under ─────────────────────────────────────────────
        if bet.bet_type in (BetType.OVER.value, BetType.UNDER.value) and not bet.is_player_prop:
            if bet.over_under_line is None:
                continue
            actual_total = float(game["total_score"])
            if actual_total == bet.over_under_line:
                outcome = Outcome.PUSH.value
            elif bet.bet_type == BetType.OVER.value:
                outcome = Outcome.WIN.value if actual_total > bet.over_under_line else Outcome.LOSE.value
            else:
                outcome = Outcome.WIN.value if actual_total < bet.over_under_line else Outcome.LOSE.value
            results.append((bet, outcome, actual_total))

        # ── Moneyline ────────────────────────────────────────────────
        elif bet.bet_type == BetType.MONEYLINE.value:
            if not bet.picked_team:
                continue
            home = game["home"]
            away = game["away"]
            if home["score"] > away["score"]:
                winner = home["name"]
            elif away["score"] > home["score"]:
                winner = away["name"]
            else:
                # Tie (unlikely in NBA)
                results.append((bet, Outcome.PUSH.value, 0.0))
                continue
            picked_lower = bet.picked_team.lower().strip()
            winner_lower = winner.lower().strip()
            outcome = Outcome.WIN.value if picked_lower in winner_lower or winner_lower in picked_lower else Outcome.LOSE.value
            results.append((bet, outcome, float(home["score"] if home["name"] == winner else away["score"])))

        # ── Player Prop ──────────────────────────────────────────────
        elif bet.is_player_prop:
            if not bet.player_name or not bet.prop_type or bet.prop_line is None:
                continue
            espn_id = game.get("espn_id", "")
            if not espn_id:
                continue
            if espn_id not in boxscore_cache:
                boxscore_cache[espn_id] = fetch_espn_boxscore(espn_id)
            boxscore = boxscore_cache[espn_id]

            # Fuzzy match player name — normalise away periods/apostrophes first
            import re as _re
            def _norm(n: str) -> str:
                return _re.sub(r"[.\'\-]", "", n).lower().strip()

            actual_stat = None
            bet_norm = _norm(bet.player_name)
            matched_player = None
            for player_name, stats in boxscore.items():
                p_norm = _norm(player_name)
                if bet_norm in p_norm or p_norm in bet_norm:
                    actual_stat = stats.get(bet.prop_type)
                    matched_player = player_name
                    break

            if matched_player is None and boxscore:
                # Player not in boxscore for a final game → DNP; void the bet (push)
                logger.warning(
                    "Player %s not in boxscore for game %s (DNP) — voiding as push",
                    bet.player_name, espn_id,
                )
                results.append((bet, Outcome.PUSH.value, 0.0))
                continue

            if actual_stat is None:
                logger.warning(
                    "Could not find stat %s for player %s in game %s",
                    bet.prop_type, bet.player_name, espn_id,
                )
                continue

            if actual_stat == bet.prop_line:
                outcome = Outcome.PUSH.value
            elif bet.bet_type == BetType.OVER.value:
                outcome = Outcome.WIN.value if actual_stat > bet.prop_line else Outcome.LOSE.value
            else:
                outcome = Outcome.WIN.value if actual_stat < bet.prop_line else Outcome.LOSE.value

            results.append((bet, outcome, actual_stat))

    return results


# ── Concrete SportService implementation ─────────────────────────────


class NBAService(SportService):
    """NBA-specific implementation backed by ESPN + The Odds API."""

    @property
    def sport_key(self) -> str:
        return "nba"

    @property
    def display_name(self) -> str:
        return "NBA"

    def fetch_scoreboard(self, date_str: Optional[str] = None) -> list[dict]:
        return fetch_espn_scoreboard(date_str)

    def fetch_boxscore(self, game_id: str) -> dict:
        return fetch_espn_boxscore(game_id)

    def fetch_odds_combined(self) -> tuple:
        return fetch_odds_combined()

    def fetch_odds_events(self) -> dict:
        return fetch_odds_events()

    def fetch_upcoming_games(self) -> list[dict]:
        return fetch_upcoming_games()

    def fetch_player_props(self, event_id: str) -> dict:
        return fetch_player_props_for_event(event_id)

    def get_todays_games(self) -> list[dict]:
        return get_todays_games()

    def get_player_props_for_game(self, game_id: str, games: Optional[list[dict]] = None) -> dict:
        return get_player_props(game_id, games)

    def resolve_pending_bets(self, pending_bets: list) -> list[tuple]:
        return resolve_pending_bets(pending_bets)

    def get_prop_markets(self) -> list[str]:
        return list(PLAYER_PROP_MARKETS)


# Register so other code can do  get_sport_service("nba")
SPORT_REGISTRY["nba"] = NBAService()
