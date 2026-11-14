"""Polite web fetching (robots.txt, rate limit) and article text extraction."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from ..utils import log


class Fetcher:
    """HTTP client that identifies itself honestly, rate-limits and respects robots.txt.

    It never tries to get around paywalls, logins or bot protection: a 401/402/403/429
    answer simply means the page is skipped.
    """

    def __init__(self, user_agent: str, delay: float = 2.0, timeout: float = 20, respect_robots: bool = True,
                 allow_file_urls: bool = False):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "en"})
        self.user_agent = user_agent
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.allow_file_urls = allow_file_urls
        self._last_request = 0.0
        self._robots: dict[str, RobotFileParser | None] = {}

    # ---- internals
    def _wait(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_request = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots or url.startswith("file://"):
            return True
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                self._wait()
                resp = self.session.get(f"{base}/robots.txt", timeout=self.timeout)
                if resp.status_code >= 500:
                    log.warning("robots.txt of %s is unavailable (%s); skipping this site to be safe",
                                parts.netloc, resp.status_code)
                    rp.parse(["User-agent: *", "Disallow: /"])  # type: ignore[union-attr]
                elif resp.status_code >= 400:
                    rp.parse([])  # type: ignore[union-attr]  # no robots.txt = allowed
                else:
                    rp.parse(resp.text.splitlines())  # type: ignore[union-attr]
            except requests.RequestException as exc:
                log.warning("Could not read robots.txt for %s (%s); skipping this site", parts.netloc, exc)
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return bool(rp and rp.can_fetch(self.user_agent, url))

    # ---- public
    def get_bytes(self, url: str, check_robots: bool = True) -> bytes | None:
        """Fetch a URL. Returns None when blocked, paywalled, disallowed or failing."""
        if url.startswith("file://"):
            if not self.allow_file_urls:
                log.warning("file:// URLs are disabled: %s", url)
                return None
            try:
                return Path(url[len("file://"):]).read_bytes()
            except OSError as exc:
                log.warning("Cannot read %s: %s", url, exc)
                return None
        if check_robots and not self.allowed(url):
            log.info("Skipped (robots.txt): %s", url)
            return None
        try:
            self._wait()
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("Request failed for %s: %s", url, exc)
            return None
        if resp.status_code in (401, 402, 403, 429):
            log.info("Skipped (blocked or paywalled, HTTP %s): %s", resp.status_code, url)
            return None
        if resp.status_code >= 400:
            log.warning("HTTP %s for %s", resp.status_code, url)
            return None
        return resp.content

    def get_text(self, url: str, check_robots: bool = True) -> str | None:
        data = self.get_bytes(url, check_robots)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")


@dataclass
class ArticleData:
    text: str
    image_url: str | None


def _og_image(soup: BeautifulSoup, base_url: str) -> str | None:
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}, {"property": "og:image:url"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return urljoin(base_url, tag["content"].strip())
    return None


def extract_article(html: str, url: str = "") -> ArticleData:
    """Extract readable article text and the main image URL from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    image = _og_image(soup, url)

    text = ""
    try:  # optional, better extractor when installed
        import trafilatura  # type: ignore

        text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    except Exception:
        text = ""

    if not text:
        for tag in soup(["script", "style", "nav", "footer", "aside", "form", "header", "noscript", "figure"]):
            tag.decompose()
        container = soup.find("article") or soup.find("main") or soup.body or soup
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = "\n".join(p for p in paragraphs if len(p) >= 40)
    return ArticleData(text=text.strip(), image_url=image)
