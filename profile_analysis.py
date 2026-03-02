"""
Profile the prop analysis page end-to-end.

Run from project root:
    source .venv/bin/activate && python profile_analysis.py

Requires ODDS_API_KEY in env (or a .env file loaded by the app).
"""

import os
import sys
import time

# ── Bootstrap Flask app ──────────────────────────────────────────────
from app import create_app
app = create_app()

# ── Helpers ──────────────────────────────────────────────────────────

class Timer:
    def __init__(self, label):
        self.label = label
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.elapsed = time.perf_counter() - self._start


def bar(seconds, scale=10.0):
    """ASCII bar: each █ = scale/20 seconds."""
    filled = int(seconds / scale * 40)
    return "█" * filled + "░" * (40 - filled)


# ── Profile inside app context ───────────────────────────────────────

with app.app_context():
    # Force-clear module caches so every phase hits real APIs/DB.
    import app.services.nba_service as nba_svc
    import app.services.value_detector as vd_mod
    nba_svc._GAMES_CACHE.clear()
    nba_svc._UPCOMING_CACHE.clear()
    vd_mod._SCORE_CACHE.clear()

    from app.services.nba_service import (
        fetch_espn_scoreboard, fetch_odds_combined, fetch_odds_events,
        fetch_player_props_for_event, get_todays_games,
    )
    from app.services.projection_engine import ProjectionEngine
    from app.services.value_detector import ValueDetector
    from app.services.context_service import is_player_available
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    print("\n" + "=" * 58)
    print("  PROP ANALYSIS PAGE — COLD LOAD PROFILE")
    print("=" * 58)
    print(f"  Date (ET): {datetime.now(ET).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 58 + "\n")

    timers = {}

    # ── Phase 1: ESPN scoreboard ─────────────────────────────────────
    with Timer("ESPN scoreboard") as t:
        today_et = datetime.now(ET).strftime("%Y%m%d")
        games_espn = fetch_espn_scoreboard(date_str=today_et)
    timers["ESPN scoreboard"] = t.elapsed
    print(f"  [1] ESPN scoreboard         {t.elapsed:6.2f}s  — {len(games_espn)} games")

    # ── Phase 2: Odds API combined (totals + h2h) ────────────────────
    with Timer("Odds API combined") as t:
        totals, h2h = fetch_odds_combined()
    timers["Odds API combined"] = t.elapsed
    print(f"  [2] Odds API combined        {t.elapsed:6.2f}s  — {len(totals)} matchups")

    # ── Phase 3: Odds API events list ───────────────────────────────
    with Timer("Odds API events") as t:
        events = fetch_odds_events()
    timers["Odds API events"] = t.elapsed
    print(f"  [3] Odds API events list     {t.elapsed:6.2f}s  — {len(events)} events")

    # Merge to get full game objects (mirrors get_todays_games logic)
    from app.services.nba_service import _matchup_key
    games = games_espn
    for game in games:
        key = _matchup_key(game["home"]["name"], game["away"]["name"])
        game["over_under_line"] = totals.get(key)
        game["odds_event_id"]   = events.get(key, "")
        ml = h2h.get(key, {})
        game["moneyline_home"] = ml.get("home")
        game["moneyline_away"] = ml.get("away")

    games_with_events = [(g, g.get("odds_event_id", "")) for g in games if g.get("odds_event_id", "")]
    print(f"\n  Games with event IDs: {len(games_with_events)} of {len(games)}\n")

    # ── Phase 4a: props fetch — sequential (baseline) ────────────────
    print("  [4a] Per-game props — SEQUENTIAL (old behaviour):")
    seq_times = []
    seq_props = {}
    for game, event_id in games_with_events:
        label = f"{game['away']['name'][:12]:12s} @ {game['home']['name'][:12]:12s}"
        with Timer(label) as t:
            p = fetch_player_props_for_event(event_id)
        seq_props[event_id] = p
        seq_times.append(t.elapsed)
        players = sum(len(v) for v in p.values())
        print(f"      {label}  {t.elapsed:5.2f}s  ({players} props)")
    seq_total = sum(seq_times)
    timers["props_sequential"] = seq_total
    print(f"      {'TOTAL sequential':36s}  {seq_total:5.2f}s")

    # ── Phase 4b: props fetch — parallel (current) ───────────────────
    print(f"\n  [4b] Per-game props — PARALLEL (current):")
    def _fetch(game_event):
        game, event_id = game_event
        t0 = time.perf_counter()
        try:
            p = fetch_player_props_for_event(event_id)
        except Exception:
            p = {}
        return game, p, time.perf_counter() - t0

    par_results = []
    with Timer("parallel_props") as par_t:
        with ThreadPoolExecutor(max_workers=min(8, len(games_with_events) or 1)) as pool:
            futures = [pool.submit(_fetch, ge) for ge in games_with_events]
            for f in as_completed(futures):
                par_results.append(f.result())
    timers["props_parallel"] = par_t.elapsed

    for game, props, elapsed in par_results:
        label = f"{game['away']['name'][:12]:12s} @ {game['home']['name'][:12]:12s}"
        players = sum(len(v) for v in props.values())
        print(f"      {label}  {elapsed:5.2f}s  ({players} props) [thread]")
    print(f"      {'TOTAL parallel (wall clock)':36s}  {par_t.elapsed:5.2f}s")

    # Rebuild payloads from parallel results
    game_props_payloads = [(g, p) for g, p, _ in par_results if p]
    all_player_names: set[str] = set()
    for _, props in game_props_payloads:
        for market_props in props.values():
            for prop in market_props:
                player = prop.get("player", "")
                if player:
                    all_player_names.add(player)

    total_props = sum(
        sum(len(v) for v in props.values())
        for _, props in game_props_payloads
    )
    print(f"\n  Unique players: {len(all_player_names)}   Total props: {total_props}")

    # ── Phase 5: player team map ─────────────────────────────────────
    with Timer("player team map") as t:
        detector = ValueDetector(ProjectionEngine())
        player_team_map = detector._build_player_team_map(all_player_names)
    timers["player_team_map"] = t.elapsed
    print(f"\n  [5] Player team map          {t.elapsed:6.2f}s  — {len(player_team_map)} resolved")

    # ── Phase 6: projection + scoring loop ───────────────────────────
    engine = ProjectionEngine()
    detector2 = ValueDetector(engine)

    with Timer("scoring loop") as t:
        all_scores = []
        for game, props in game_props_payloads:
            espn_id    = game.get("espn_id", "")
            home_team  = game.get("home", {}).get("name", "")
            away_team  = game.get("away", {}).get("name", "")
            home_abbr  = (game.get("home", {}).get("abbr") or "").upper()
            away_abbr  = (game.get("away", {}).get("abbr") or "").upper()

            for market_key, market_props in props.items():
                for prop in market_props:
                    player = prop.get("player", "")
                    if not player or not is_player_available(player):
                        continue
                    team_name, opponent_name, is_home = detector2._resolve_game_context_for_player(
                        player, home_team, away_team, home_abbr, away_abbr, player_team_map
                    )
                    score = detector2.score_prop(
                        player_name=player,
                        prop_type=market_key,
                        line=prop.get("line", 0),
                        over_odds=prop.get("over_odds", -110),
                        under_odds=prop.get("under_odds", -110),
                        opponent_name=opponent_name,
                        team_name=team_name,
                        is_home=is_home,
                        game_id=espn_id,
                    )
                    all_scores.append(score)
    timers["scoring_loop"] = t.elapsed
    scored = len(all_scores)
    print(f"  [6] Projection/scoring loop  {t.elapsed:6.2f}s  — {scored} props scored")

    # ── Summary ──────────────────────────────────────────────────────
    total_cold = (
        timers["ESPN scoreboard"]
        + timers["Odds API combined"]
        + timers["Odds API events"]
        + timers["props_parallel"]
        + timers["player_team_map"]
        + timers["scoring_loop"]
    )

    print("\n" + "=" * 58)
    print("  BREAKDOWN SUMMARY")
    print("=" * 58)
    phases = [
        ("ESPN scoreboard",         timers["ESPN scoreboard"]),
        ("Odds API combined",       timers["Odds API combined"]),
        ("Odds API events list",    timers["Odds API events"]),
        ("Props fetch (parallel)",  timers["props_parallel"]),
        ("Props fetch (would-be sequential)", timers["props_sequential"]),
        ("Player team map (DB)",    timers["player_team_map"]),
        ("Projection/scoring loop", timers["scoring_loop"]),
    ]
    max_t = max(v for _, v in phases)
    for label, elapsed in phases:
        pct = elapsed / total_cold * 100 if total_cold else 0
        sep = "  ← saved" if "sequential" in label else ""
        print(f"  {label:<38s}  {elapsed:5.2f}s  ({pct:4.0f}%){sep}")

    print("-" * 58)
    print(f"  COLD LOAD TOTAL (parallel)   {total_cold:6.2f}s")
    saved = timers["props_sequential"] - timers["props_parallel"]
    print(f"  Time saved vs sequential     {saved:+6.2f}s  (props parallelization)")
    print(f"  WARM LOAD (cache hit)         ~0.00s")
    print("=" * 58 + "\n")
