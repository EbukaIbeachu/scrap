"""Stage 1: collect stories from RSS feeds (and optional news APIs) into SQLite."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..context import Ctx
from ..utils import log
from .article import Fetcher, extract_article
from .newsapi import build_provider
from .rss import FeedEntry, parse_feed


def make_fetcher(ctx: Ctx) -> Fetcher:
    return Fetcher(
        user_agent=ctx.get("general.user_agent"),
        delay=float(ctx.get("general.request_delay_seconds", 2.0)),
        timeout=float(ctx.get("general.request_timeout", 20)),
        respect_robots=bool(ctx.get("collector.respect_robots", True)),
        allow_file_urls=bool(ctx.get("general.allow_file_urls", False)),
    )


def _store_entry(ctx: Ctx, fetcher: Fetcher, outlet: str, entry: FeedEntry) -> bool:
    """Fetch the article for one entry and store it. Returns True if a new story was added."""
    db = ctx.db
    if db.story_exists(entry.link):
        return False
    min_chars = int(ctx.get("collector.min_article_chars", 600))
    text = entry.content if len(entry.content) >= min_chars else ""
    images = list(entry.image_urls)

    if not text and ctx.get("collector.fetch_full_text", True):
        html = fetcher.get_text(entry.link)
        if html:
            art = extract_article(html, entry.link)
            text = art.text
            if art.image_url:
                images.insert(0, art.image_url)  # og:image is usually the best picture

    quality = "full" if len(text) >= min_chars else "summary_only"
    if quality == "summary_only":
        text = text or entry.content or entry.summary
    new_id = db.add_story(
        outlet=outlet,
        url=entry.link,
        title=entry.title,
        summary=entry.summary,
        text=text,
        text_quality=quality,
        published=entry.published,
        image_urls=images,
    )
    if new_id:
        log.info("  + %s [%s] %s", outlet, quality, entry.title[:80])
    return new_id is not None


def run_collect(ctx: Ctx) -> int:
    """Collect new stories from every enabled outlet. Returns the number of new stories."""
    fetcher = make_fetcher(ctx)
    per_outlet = int(ctx.get("collector.max_stories_per_outlet", 5))
    added = 0

    outlets = [o for o in ctx.get("outlets", []) if o.get("enabled", True)]
    if not outlets and not ctx.get("news_apis"):
        log.warning("No outlets configured. Add feeds under 'outlets' in config.yaml.")

    for outlet in outlets:
        name, url = outlet.get("name", "Unknown"), outlet.get("rss_url", "")
        limit = int(outlet.get("max_stories", per_outlet))
        log.info("Collecting from %s", name)
        try:
            data = fetcher.get_bytes(url, check_robots=False)
            if data is None:
                log.warning("Skipped %s (feed not available)", name)
                continue
            entries = parse_feed(data)
        except ET.ParseError as exc:
            log.warning("Skipped %s (feed is not valid XML: %s)", name, exc)
            continue
        except Exception as exc:  # one bad source must never stop the run
            log.warning("Skipped %s (%s)", name, exc)
            continue
        entries.sort(key=lambda e: e.published or "", reverse=True)
        for entry in entries[:limit]:
            try:
                added += _store_entry(ctx, fetcher, name, entry)
            except Exception as exc:
                log.warning("  ! Failed on '%s': %s", entry.title[:60], exc)

    for conf in ctx.get("news_apis", []):
        if not conf.get("enabled", False):
            continue
        provider = build_provider(conf, float(ctx.get("general.request_timeout", 20)))
        if provider is None:
            continue
        try:
            for outlet_name, entry in provider.fetch()[:per_outlet * 2]:
                added += _store_entry(ctx, fetcher, outlet_name, entry)
        except Exception as exc:
            log.warning("News API %s failed: %s", conf.get("provider"), exc)

    log.info("Collected %d new stories (%d in database)", added, ctx.db.count_stories())
    return added
