"""Optional news-API sources behind one small interface (headlines + snippets, legally).

These are NOT exercised by the offline tests; check each provider's current docs and
terms before relying on them. Enable them in ``config.yaml`` under ``news_apis``.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

import requests

from ..utils import log
from .rss import FeedEntry, html_to_text, parse_date


class NewsAPIProvider(ABC):
    name = "base"

    def __init__(self, params: dict, timeout: float = 20):
        self.params = params
        self.timeout = timeout

    @abstractmethod
    def fetch(self) -> list[tuple[str, FeedEntry]]:
        """Return a list of (outlet_name, entry)."""


class GNewsProvider(NewsAPIProvider):
    """https://gnews.io  (key in GNEWS_API_KEY)."""

    name = "gnews"

    def fetch(self) -> list[tuple[str, FeedEntry]]:
        key = os.getenv("GNEWS_API_KEY")
        if not key:
            log.warning("GNEWS_API_KEY is not set; skipping GNews")
            return []
        query = {"category": "general", "lang": "en", "max": 10, **self.params, "apikey": key}
        resp = requests.get("https://gnews.io/api/v4/top-headlines", params=query, timeout=self.timeout)
        resp.raise_for_status()
        out = []
        for a in resp.json().get("articles", []):
            entry = FeedEntry(
                title=html_to_text(a.get("title", "")),
                link=a.get("url", ""),
                summary=html_to_text(a.get("description", "")),
                content=html_to_text(a.get("content", "")),
                published=parse_date(a.get("publishedAt")),
                image_urls=[a["image"]] if a.get("image") else [],
            )
            if entry.title and entry.link:
                out.append(((a.get("source") or {}).get("name") or "GNews", entry))
        return out


class NewsDataProvider(NewsAPIProvider):
    """https://newsdata.io  (key in NEWSDATA_API_KEY)."""

    name = "newsdata"

    def fetch(self) -> list[tuple[str, FeedEntry]]:
        key = os.getenv("NEWSDATA_API_KEY")
        if not key:
            log.warning("NEWSDATA_API_KEY is not set; skipping NewsData")
            return []
        query = {"language": "en", **self.params, "apikey": key}
        resp = requests.get("https://newsdata.io/api/1/latest", params=query, timeout=self.timeout)
        resp.raise_for_status()
        out = []
        for a in resp.json().get("results", []):
            entry = FeedEntry(
                title=html_to_text(a.get("title", "")),
                link=a.get("link", ""),
                summary=html_to_text(a.get("description") or ""),
                content=html_to_text(a.get("content") or ""),
                published=parse_date((a.get("pubDate") or "").replace(" ", "T")),
                image_urls=[a["image_url"]] if a.get("image_url") else [],
            )
            if entry.title and entry.link:
                out.append((a.get("source_name") or a.get("source_id") or "NewsData", entry))
        return out


PROVIDERS = {"gnews": GNewsProvider, "newsdata": NewsDataProvider}


def build_provider(conf: dict, timeout: float) -> NewsAPIProvider | None:
    cls = PROVIDERS.get(str(conf.get("provider", "")).lower())
    if cls is None:
        log.warning("Unknown news API provider: %s", conf.get("provider"))
        return None
    return cls(conf.get("params") or {}, timeout)
