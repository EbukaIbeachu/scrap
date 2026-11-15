#!/usr/bin/env python3
"""
Fetch today's full-time football scores from api-football.com (v3)
and inject them into the pipeline DB.

The API key is injected automatically by the proxy — no env variable needed.

Usage:
    python fetch_scores.py --config config-scores.yaml
    python fetch_scores.py --config config-female-scores.yaml

Then build the video:
    python main.py run-all --config config-scores.yaml --skip-collect -v --no-approve-prompt --no-download-prompt
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

API_BASE = "https://v3.football.api-sports.io"

# League IDs to fetch — covers all major competitions
# Men's leagues
MEN_LEAGUE_IDS = [
    2,    # UEFA Champions League
    3,    # UEFA Europa League
    848,  # UEFA Conference League
    4,    # UEFA Euro (European Championship)
    5,    # UEFA Nations League
    6,    # FIFA World Cup
    39,   # Premier League (England)
    40,   # Championship (England)
    61,   # Ligue 1 (France)
    62,   # Ligue 2 (France)
    71,   # Série A (Brazil)
    78,   # Bundesliga (Germany)
    79,   # 2. Bundesliga (Germany)
    88,   # Eredivisie (Netherlands)
    94,   # Primeira Liga (Portugal)
    106,  # Ekstraklasa (Poland)
    119,  # Superliga (Denmark)
    135,  # Serie A (Italy)
    136,  # Serie B (Italy)
    140,  # La Liga (Spain)
    141,  # La Liga 2 (Spain)
    143,  # Copa del Rey (Spain)
    197,  # Super League (Greece)
    203,  # Süper Lig (Turkey)
    207,  # Super League (Switzerland)
    218,  # Bundesliga (Austria)
    235,  # Premier League (Russia)
    253,  # Major League Soccer (USA)
    262,  # Liga MX (Mexico)
    307,  # Saudi Pro League
    351,  # AFC Champions League
]

# Women's leagues
WOMEN_LEAGUE_IDS = [
    754,  # UEFA Women's Champions League
    755,  # FIFA Women's World Cup
    756,  # UEFA Women's Euro
    757,  # Women's Super League (England)
    758,  # D1 Arkema (France)
    760,  # Frauen-Bundesliga (Germany)
    761,  # Primera Iberdrola (Spain)
    762,  # Serie A Femminile (Italy)
    763,  # NWSL (USA)
]


def api_get(path: str, params: dict | None = None) -> dict:
    """Call the API — key is injected by the proxy."""
    url = f"{API_BASE}/{path.lstrip('/')}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": "proxy-injected",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def get_todays_fixtures(league_ids: list[int], date_str: str) -> list[dict]:
    """Fetch all finished fixtures for the given leagues on date_str."""
    fixtures = []
    # Batch by date — one call gets all leagues for that day
    data = api_get("fixtures", {"date": date_str, "status": "FT-AET-PEN"})
    if data.get("errors"):
        err = data["errors"]
        raise RuntimeError(f"API error: {err}")
    for fix in data.get("response", []):
        lid = fix.get("league", {}).get("id")
        if lid in league_ids:
            fixtures.append(fix)
    return fixtures


def format_story(fix: dict) -> dict:
    """Turn a fixture response object into a pipeline story dict."""
    league = fix.get("league", {})
    teams = fix.get("teams", {})
    goals = fix.get("goals", {})
    score = fix.get("score", {})
    fixture_info = fix.get("fixture", {})

    home = teams.get("home", {}).get("name", "?")
    away = teams.get("away", {}).get("name", "?")
    home_goals = goals.get("home", 0) or 0
    away_goals = goals.get("away", 0) or 0
    competition = league.get("name", "Football")
    country = league.get("country", "")
    round_name = league.get("round", "")
    fixture_id = fixture_info.get("id", 0)

    score_str = f"{home_goals}-{away_goals}"
    if home_goals > away_goals:
        result_line = f"{home} beat {away} {score_str}"
    elif away_goals > home_goals:
        result_line = f"{away} beat {home} {away_goals}-{home_goals}"
    else:
        result_line = f"{home} and {away} drew {score_str}"

    # Check for extra time / penalties
    ft_status = fixture_info.get("status", {}).get("short", "FT")
    if ft_status == "AET":
        result_line += " (after extra time)"
    elif ft_status == "PEN":
        pen = score.get("penalty", {})
        result_line += f" (penalties: {home} {pen.get('home','?')}-{pen.get('away','?')} {away})"

    comp_line = f"{competition}"
    if country and country not in competition:
        comp_line = f"{country} {competition}"
    if round_name:
        comp_line += f" — {round_name}"

    title = f"{home} {score_str} {away}"
    text = (
        f"{result_line}. "
        f"{comp_line}. "
        f"Final score: {home} {score_str} {away}."
    )

    # Build a unique stable URL from fixture ID
    url = f"https://v3.football.api-sports.io/fixtures?id={fixture_id}"

    pub_ts = fixture_info.get("timestamp")
    pub_iso = (
        datetime.fromtimestamp(pub_ts, tz=timezone.utc).isoformat()
        if pub_ts else datetime.now(timezone.utc).isoformat()
    )

    # Try to get a team logo as an image
    home_logo = teams.get("home", {}).get("logo", "")
    away_logo = teams.get("away", {}).get("logo", "")
    image_urls = [u for u in [home_logo, away_logo] if u]

    return {
        "outlet": f"{comp_line}",
        "url": url,
        "title": title,
        "summary": text,
        "text": text,
        "text_quality": "full",
        "published": pub_iso,
        "image_urls": image_urls,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config-scores.yaml")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--women", action="store_true", help="fetch women's leagues instead")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    data_dir = Path(cfg["general"]["data_dir"])
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Pick league list based on config name or --women flag
    is_women = args.women or "female" in args.config.lower() or "women" in args.config.lower()
    league_ids = set(WOMEN_LEAGUE_IDS if is_women else MEN_LEAGUE_IDS)

    print(f"Fetching {'women' if is_women else 'men'}'s FT scores for {date_str}...")

    # Test API key
    try:
        status = api_get("status")
        if status.get("errors"):
            print(f"ERROR: API key problem: {status['errors']}", file=sys.stderr)
            sys.exit(1)
        acct = status.get("response", {}).get("account", {})
        sub = status.get("response", {}).get("subscription", {})
        requests_info = status.get("response", {}).get("requests", {})
        print(f"  API account: {acct.get('firstname','')} {acct.get('lastname','')} "
              f"| Plan: {sub.get('plan','?')} "
              f"| Requests today: {requests_info.get('current','?')}/{requests_info.get('limit_day','?')}")
    except Exception as exc:
        print(f"ERROR: Could not reach API: {exc}", file=sys.stderr)
        sys.exit(1)

    # Fetch fixtures
    try:
        fixtures = get_todays_fixtures(list(league_ids), date_str)
    except Exception as exc:
        print(f"ERROR fetching fixtures: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"  Found {len(fixtures)} finished fixtures")

    if not fixtures:
        print("No finished fixtures today — nothing injected.")
        return

    # Import pipeline DB
    sys.path.insert(0, str(Path(__file__).parent))
    from src.db import Database

    db = Database(data_dir / "news.db")

    injected = 0
    for fix in fixtures:
        story = format_story(fix)
        story_id = db.add_story(
            outlet=story["outlet"],
            url=story["url"],
            title=story["title"],
            summary=story["summary"],
            text=story["text"],
            text_quality=story["text_quality"],
            published=story["published"],
            image_urls=story["image_urls"],
        )
        if story_id is not None:
            injected += 1
            print(f"  + {story['title']}")

    print(f"\nInjected {injected} score fixtures into {data_dir}/news.db")
    print(f"\nNow run:")
    print(f"  python main.py run-all --config {args.config} --skip-collect -v --no-approve-prompt --no-download-prompt")


if __name__ == "__main__":
    main()
