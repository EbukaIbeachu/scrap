#!/usr/bin/env python3
"""
Fetch only full-time score articles from BBC Sport RSS feeds and inject
them into the pipeline DB for a scores config.

Usage:
    python fetch_scores.py --config config-scores.yaml
    python fetch_scores.py --config config-female-scores.yaml
    # Then run the pipeline skipping collect:
    python main.py run-all --config config-scores.yaml --skip-collect --no-approve-prompt --no-download-prompt
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# Score patterns in BBC Sport article titles
# e.g. "Arsenal 3-1 Chelsea", "Real Madrid 2–0 Barcelona", "Man City 4-0 Wolves"
SCORE_RE = re.compile(r'\b\d{1,2}[-–]\d{1,2}\b')

# Phrases that confirm it's a result (not a preview)
RESULT_WORDS = {
    "match report", "scorecard", "highlights", "result", "beaten",
    "thrashed", "beat ", "wins ", "win ", "draws", "drew", "defeat",
    "victory", "comeback", "hat-trick", "brace", "full time", "full-time",
    "aggregate", "second leg", "first leg",
}

PROXY = None  # picked up from env automatically by urllib


def has_score(title: str, description: str = "") -> bool:
    """Return True if the title contains a score pattern."""
    return bool(SCORE_RE.search(title))


def is_result(title: str, description: str = "") -> bool:
    """Return True if the article is about a completed match."""
    combined = (title + " " + description).lower()
    return has_score(title) or any(w in combined for w in RESULT_WORDS)


def fetch_feed(url: str, timeout: int = 20) -> list[dict]:
    """Fetch an RSS feed and return items as dicts."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NewsHighlightsPersonal/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_bytes = resp.read()
    except Exception as exc:
        print(f"  WARN: could not fetch {url}: {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"  WARN: bad XML from {url}: {exc}", file=sys.stderr)
        return []

    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    items = []
    for item in root.iter("item"):
        def t(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        pub_raw = t("pubDate")
        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(pub_raw).astimezone(timezone.utc)
            pub_iso = pub_dt.isoformat()
        except Exception:
            pub_iso = datetime.now(timezone.utc).isoformat()

        items.append({
            "url": t("link") or t("guid"),
            "title": t("title"),
            "summary": re.sub(r'<[^>]+>', '', t("description")),
            "published": pub_iso,
        })
    return items


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject full-time scores into the pipeline DB")
    parser.add_argument("--config", default="config-scores.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    data_dir = Path(cfg["general"]["data_dir"])
    lookback_hours = int(cfg.get("collector", {}).get("lookback_hours", 24))
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    # Import pipeline DB (requires being run from project root)
    sys.path.insert(0, str(Path(__file__).parent))
    from src.db import Database

    db = Database(data_dir / "news.db")

    outlets = [o for o in cfg.get("outlets", []) if o.get("enabled")]
    print(f"Fetching scores from {len(outlets)} feeds (last {lookback_hours}h)...")

    total_injected = 0
    for outlet in outlets:
        name = outlet["name"]
        url = outlet["rss_url"]
        print(f"  → {name}")
        items = fetch_feed(url)
        injected = 0
        for item in items:
            # Only within lookback window
            try:
                pub = datetime.fromisoformat(item["published"])
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < since:
                    continue
            except Exception:
                pass

            title = item["title"]
            summary = item["summary"]

            # Only keep articles that are actual match results with a score
            if not is_result(title, summary):
                continue

            # Build a clean text that leads with the score/result facts
            score_match = SCORE_RE.search(title)
            if score_match:
                score = score_match.group(0)
                text = f"{title}. Final score: {score}. {summary}".strip()
            else:
                text = f"{title}. {summary}".strip()

            story_id = db.add_story(
                outlet=name,
                url=item["url"],
                title=title,
                summary=summary,
                text=text,
                text_quality="full",
                published=item["published"],
            )
            if story_id is not None:
                injected += 1
                print(f"      + {title[:80]}")

        total_injected += injected

    print(f"\nInjected {total_injected} score articles into {data_dir}/news.db")
    print("Now run:")
    print(f"  python main.py run-all --config {args.config} --skip-collect --no-approve-prompt --no-download-prompt")


if __name__ == "__main__":
    main()
