"""Stock photos from Pexels and Pixabay (free APIs, keys in .env)."""
from __future__ import annotations

import os
from pathlib import Path

import requests

from ..collector.article import Fetcher
from ..utils import log
from .article_images import download_image, fit_image


def _pexels(query: str, key: str, timeout: float, landscape: bool) -> list[dict]:
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": key},
        params={"query": query, "per_page": 6, "orientation": "landscape" if landscape else "portrait"},
        timeout=timeout,
    )
    resp.raise_for_status()
    out = []
    for p in resp.json().get("photos", []):
        src = p.get("src", {})
        url = src.get("large2x") or src.get("large") or src.get("original")
        if url:
            out.append({
                "url": url,
                "license_note": "Pexels License (free to use, including commercially; attribution appreciated)",
                "credit": f"Photo: {p.get('photographer', 'Unknown')} / Pexels",
                "page": p.get("url", ""),
            })
    return out


def _pixabay(query: str, key: str, timeout: float, landscape: bool) -> list[dict]:
    resp = requests.get(
        "https://pixabay.com/api/",
        params={"key": key, "q": query, "image_type": "photo", "per_page": 6,
                "orientation": "horizontal" if landscape else "vertical", "safesearch": "true"},
        timeout=timeout,
    )
    resp.raise_for_status()
    out = []
    for p in resp.json().get("hits", []):
        url = p.get("largeImageURL") or p.get("webformatURL")
        if url:
            out.append({
                "url": url,
                "license_note": "Pixabay License (free to use, including commercially)",
                "credit": f"Image: {p.get('user', 'Unknown')} / Pixabay",
                "page": p.get("pageURL", ""),
            })
    return out


def fetch_stock_images(ctx, story: dict, keywords: list[str], fetcher: Fetcher, out_dir: Path) -> list[dict]:
    """Download up to ``images_per_story`` stock photos matching the keywords."""
    pexels = os.getenv(ctx.get("images.pexels_api_key_env", "PEXELS_API_KEY"), "")
    pixabay = os.getenv(ctx.get("images.pixabay_api_key_env", "PIXABAY_API_KEY"), "")
    if not (pexels or pixabay):
        log.info("  stock: no PEXELS_API_KEY / PIXABAY_API_KEY set")
        return []
    limit = int(ctx.get("images.images_per_story", 2))
    min_w, min_h = int(ctx.get("images.min_width", 500)), int(ctx.get("images.min_height", 300))
    timeout = float(ctx.get("general.request_timeout", 20))
    landscape = ctx.video_size[0] >= ctx.video_size[1]
    query = " ".join(keywords[:3]) or story["title"]

    candidates: list[dict] = []
    for fn, key in ((_pexels, pexels), (_pixabay, pixabay)):
        if not key:
            continue
        try:
            candidates += fn(query, key, timeout, landscape)
        except requests.RequestException as exc:
            log.warning("  stock search failed (%s)", exc)

    results: list[dict] = []
    for cand in candidates:
        if len(results) >= limit:
            break
        data = download_image(fetcher, cand["url"], min_w, min_h, check_robots=False)
        if not data:
            continue
        dest = fit_image(data, out_dir / f"stock_{len(results) + 1}.jpg", ctx.work_size)
        results.append({
            "kind": "stock", "path": str(dest), "source_url": cand["page"] or cand["url"],
            "license_note": cand["license_note"], "credit": cand["credit"],
        })
    return results
