"""Download, validate and crop images; article-image sourcing."""
from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from ..collector.article import Fetcher
from ..utils import log


def fit_image(source: bytes | Path, dest: Path, size: tuple[int, int], centering: tuple[float, float] = (0.5, 0.4)) -> Path:
    """Cover-crop an image to exactly ``size`` and save it as JPEG.

    ``centering`` keeps the crop slightly above the middle so faces are less likely to be cut.
    """
    img = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, size, Image.Resampling.LANCZOS, centering=centering)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=90)
    return dest


def check_image(data: bytes, min_w: int, min_h: int) -> tuple[bool, str]:
    """Is this a decodable image that is big enough?"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            w, h = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        return False, "not a readable image"
    if w < min_w or h < min_h:
        return False, f"too small ({w}x{h})"
    return True, "ok"


def download_image(fetcher: Fetcher, url: str, min_w: int, min_h: int, check_robots: bool = True) -> bytes | None:
    data = fetcher.get_bytes(url, check_robots=check_robots)
    if not data:
        return None
    ok, why = check_image(data, min_w, min_h)
    if not ok:
        log.info("  skipped image (%s): %s", why, url[:100])
        return None
    return data


def fetch_article_images(ctx, story: dict, fetcher: Fetcher, out_dir: Path) -> list[dict]:
    """Main image(s) the outlet published with the story (personal-use only)."""
    limit = int(ctx.get("images.images_per_story", 2))
    min_w, min_h = int(ctx.get("images.min_width", 500)), int(ctx.get("images.min_height", 300))
    results: list[dict] = []
    for i, url in enumerate(story.get("image_urls", [])):
        if len(results) >= limit:
            break
        try:
            data = download_image(fetcher, url, min_w, min_h)
            if not data:
                continue
            dest = fit_image(data, out_dir / f"article_{len(results) + 1}.jpg", ctx.work_size)
            host = urlparse(url).netloc or story["outlet"]
            results.append({
                "kind": "article",
                "path": str(dest),
                "source_url": url,
                "license_note": f"(c) {story['outlet']} / original rights holder. Personal use only; "
                                f"do not publish or monetize without permission.",
                "credit": f"Image: {story['outlet']}" if host else "",
            })
        except Exception as exc:
            log.warning("  image failed (%s): %s", exc, url[:100])
    return results
