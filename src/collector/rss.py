"""Small, dependency-free RSS 2.0 / RSS 1.0 / Atom parser."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

MEDIA_NS = "http://search.yahoo.com/mrss/"


@dataclass
class FeedEntry:
    title: str
    link: str
    summary: str = ""
    content: str = ""  # full text from <content:encoded>, if the feed provides it
    published: str | None = None  # ISO 8601 UTC
    image_urls: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def html_to_text(html: str) -> str:
    """Strip tags and collapse whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _images_in_html(html: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    return [img["src"] for img in soup.find_all("img") if img.get("src")]


def parse_date(value: str | None) -> str | None:
    """Parse RFC 822 or ISO 8601 dates into ISO 8601 UTC. Returns None if unparseable."""
    if not value:
        return None
    value = value.strip()
    dt: datetime | None = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_feed(data: bytes) -> list[FeedEntry]:
    """Parse feed XML into entries. Raises ``ET.ParseError`` for invalid XML."""
    root = ET.fromstring(data)
    entries: list[FeedEntry] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = link = summary = content = date = ""
        images: list[str] = []
        for child in el:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if child.tag.startswith(f"{{{MEDIA_NS}}}"):
                # media:title / media:description describe the picture, not the story, so they
                # must never overwrite the real headline or summary.
                if name in ("content", "thumbnail"):
                    url = child.get("url")
                    kind = (child.get("medium") or child.get("type") or "image").lower()
                    if url and ("image" in kind or name == "thumbnail"):
                        images.append(url)
                continue
            if name == "title":
                title = html_to_text(text)
            elif name == "link":
                if child.get("href"):  # Atom
                    if child.get("rel", "alternate") == "alternate" or not link:
                        link = child.get("href", "").strip()
                elif text:
                    link = link or text
            elif name in ("description", "summary"):
                summary = summary or text
            elif name in ("encoded", "content"):
                content = content or text
            elif name in ("pubDate", "published", "updated", "date") and not date:
                date = text
            elif name == "enclosure":
                url = child.get("url")
                if url and (child.get("type") or "").lower().startswith("image"):
                    images.append(url)
        if not title or not link:
            continue
        images += _images_in_html(content) + _images_in_html(summary)
        entries.append(
            FeedEntry(
                title=title,
                link=link,
                summary=html_to_text(summary),
                content=html_to_text(content),
                published=parse_date(date),
                image_urls=list(dict.fromkeys(images)),
            )
        )
    return entries
