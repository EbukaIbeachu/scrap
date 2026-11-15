#!/usr/bin/env python3
"""
Fetch today's full-time football scores and inject them into the pipeline DB.

Tries api-football.com v3 first (requires API key saved via proxy credential
form for v3.football.api-sports.io). If the key is missing, falls back to
filtering BBC Sport RSS feeds for score-pattern headlines.

Usage:
    python fetch_scores.py --config config-scores.yaml
    python fetch_scores.py --config config-female-scores.yaml
    python fetch_scores.py --config config-scores.yaml --rss   # force RSS mode

Then build the video:
    python main.py run-all --config config-scores.yaml --skip-collect -v --no-approve-prompt --no-download-prompt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime

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


# ── RSS fallback ─────────────────────────────────────────────────────────────

SCORE_RE = re.compile(r'\b\d{1,2}[-–]\d{1,2}\b')
RESULT_WORDS = {
    "match report", "scorecard", "highlights", "result", "beaten",
    "thrashed", "beat ", "wins ", "win ", "draws", "drew", "defeat",
    "victory", "comeback", "hat-trick", "brace", "full time", "full-time",
    "aggregate", "second leg", "first leg",
}


def _rss_is_result(title: str, description: str = "") -> bool:
    combined = (title + " " + description).lower()
    return bool(SCORE_RE.search(title)) or any(w in combined for w in RESULT_WORDS)


def fetch_rss_scores(cfg: dict, lookback_hours: int = 24) -> list[dict]:
    """Fall back to filtering BBC RSS feeds for score-pattern headlines."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    stories = []
    ns = {"media": "http://search.yahoo.com/mrss/"}

    for outlet in cfg.get("outlets", []):
        if not outlet.get("enabled", False):
            continue
        url = outlet["rss_url"]
        name = outlet["name"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": cfg["general"].get("user_agent", "Mozilla/5.0")})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
        except Exception as exc:
            print(f"  [skip] {name}: {exc}")
            continue

        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub_raw = item.findtext("pubDate") or ""

            # Parse publish time
            try:
                pub_dt = parsedate_to_datetime(pub_raw).astimezone(timezone.utc)
            except Exception:
                pub_dt = datetime.now(timezone.utc)

            if pub_dt < cutoff:
                continue
            if not _rss_is_result(title, desc):
                continue

            # Grab thumbnail if present
            thumb = ""
            for tag in ("media:thumbnail", "media:content"):
                el = item.find(tag, ns)
                if el is not None:
                    thumb = el.get("url", "")
                    break

            stories.append({
                "outlet": name,
                "url": link,
                "title": title,
                "summary": desc,
                "text": desc,
                "text_quality": "summary_only",
                "published": pub_dt.isoformat(),
                "image_urls": [thumb] if thumb else [],
            })

    return stories


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config-scores.yaml")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--women", action="store_true", help="fetch women's leagues instead")
    parser.add_argument("--rss", action="store_true", help="force RSS fallback mode")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    data_dir = Path(cfg["general"]["data_dir"])
    lookback = cfg.get("collector", {}).get("lookback_hours", 24)
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    is_women = args.women or "female" in args.config.lower() or "women" in args.config.lower()

    # Import pipeline DB
    sys.path.insert(0, str(Path(__file__).parent))
    from src.db import Database
    db = Database(data_dir / "news.db")

    # ── Try API first ──────────────────────────────────────────────────────
    use_rss = args.rss
    if not use_rss:
        print(f"Trying api-football.com for {'women' if is_women else 'men'}'s FT scores ({date_str})...")
        try:
            status = api_get("status")
            if status.get("errors"):
                print(f"  API key not set — falling back to RSS filtering.")
                use_rss = True
            else:
                acct = status.get("response", {}).get("account", {})
                sub = status.get("response", {}).get("subscription", {})
                req_info = status.get("response", {}).get("requests", {})
                print(f"  Connected: {acct.get('firstname','')} {acct.get('lastname','')} "
                      f"| {sub.get('plan','?')} plan "
                      f"| {req_info.get('current','?')}/{req_info.get('limit_day','?')} req today")
        except Exception as exc:
            print(f"  API unreachable ({exc}) — falling back to RSS filtering.")
            use_rss = True

    # ── API mode ───────────────────────────────────────────────────────────
    if not use_rss:
        league_ids = set(WOMEN_LEAGUE_IDS if is_women else MEN_LEAGUE_IDS)
        try:
            fixtures = get_todays_fixtures(list(league_ids), date_str)
        except Exception as exc:
            print(f"  Fixture fetch failed ({exc}) — falling back to RSS filtering.")
            use_rss = True

    if not use_rss:
        print(f"  Found {len(fixtures)} finished fixtures")
        stories = [format_story(f) for f in fixtures]
    else:
        # ── RSS fallback ───────────────────────────────────────────────────
        print(f"RSS fallback: filtering feeds for score headlines (last {lookback}h)...")
        stories = fetch_rss_scores(cfg, lookback_hours=lookback)
        print(f"  Found {len(stories)} score articles")

    if not stories:
        print("Nothing to inject — no scores found.")
        return

    injected = 0
    for s in stories:
        sid = db.add_story(
            outlet=s["outlet"],
            url=s["url"],
            title=s["title"],
            summary=s["summary"],
            text=s["text"],
            text_quality=s["text_quality"],
            published=s["published"],
            image_urls=s["image_urls"],
        )
        if sid is not None:
            injected += 1
            print(f"  + {s['title']}")

    print(f"\nInjected {injected} stories into {data_dir}/news.db")
    print(f"\nNow run:")
    print(f"  python main.py run-all --config {args.config} --skip-collect -v --no-approve-prompt --no-download-prompt")


if __name__ == "__main__":
    main()
