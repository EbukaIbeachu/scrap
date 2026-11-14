"""Image-sourcing pipeline (Stage 2 orchestrator).

Decides which source(s) to try per story based on ``images.image_mode``, keeps
the stage idempotent (stories that already have images are skipped), and never
lets one bad story stop the run.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database, ImageRecord
from ..utils import HttpClient
from . import article_images, ai_images, fallback_cards, stock

logger = logging.getLogger(__name__)

_VALID_MODES = {"article", "stock", "ai", "cards", "auto"}


def _resolve_order(mode: str, cfg: Config) -> list[str]:
    """Return the ordered list of image sources to attempt for each story."""
    if mode == "auto":
        order = list(cfg.images.auto_order)
    else:
        order = [mode]

    # monetization_safe forbids re-using copyrighted news photos.
    if cfg.monetization_safe:
        order = [m for m in order if m != "article"]

    # Always keep a guaranteed fallback so every story ends up with at least one image.
    if "cards" not in order:
        order.append("cards")
    return order


def _source_story(
    story,
    order: list[str],
    http: HttpClient,
    cfg: Config,
    out_dir: Path,
) -> list[ImageRecord]:
    """Try each source in order and return the first non-empty result."""
    for source in order:
        if source == "article":
            records = article_images.collect_article_images(story, http, cfg, out_dir)
        elif source == "stock":
            records = stock.collect_stock_images(story, http, cfg, out_dir)
        elif source == "ai":
            records = ai_images.collect_ai_images(story, http, cfg, out_dir)
        elif source == "cards":
            records = fallback_cards.make_headline_card(story, cfg, out_dir)
        else:
            logger.debug("Unknown image source '%s' — skipping", source)
            records = []
        if records:
            return records
    return []


def run_images(
    cfg: Config,
    db: Database,
    *,
    mode_override: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Source images for stored stories.

    Args:
        cfg: Full configuration.
        db: Database handle (schema must be initialised).
        mode_override: Force a specific ``image_mode`` for this run.
        limit: Only process the most recent ``limit`` stories.

    Returns:
        A summary dict with counts and a breakdown by source type.
    """
    mode = (mode_override or cfg.images.mode).lower()
    if mode not in _VALID_MODES:
        logger.warning("Unknown image_mode '%s' — falling back to 'auto'", mode)
        mode = "auto"

    order = _resolve_order(mode, cfg)
    logger.info("Image mode: %s (order: %s)", mode, ", ".join(order))

    http = HttpClient(
        user_agent=cfg.collector.user_agent,
        timeout=cfg.collector.request_timeout_seconds,
        delay=cfg.collector.request_delay_seconds,
        respect_robots=cfg.collector.respect_robots_txt,
    )

    stories = db.list_stories(limit)
    date_dir = datetime.now().strftime("%Y-%m-%d")
    summary: dict[str, Any] = {
        "mode": mode,
        "stories": 0,
        "images_added": 0,
        "skipped_existing": 0,
        "no_image": 0,
        "by_source": {},
    }

    for story in stories:
        story_id = story["id"]

        if db.story_has_images(story_id):
            summary["skipped_existing"] += 1
            logger.debug("Story %s already has images — skipped", story_id)
            continue

        summary["stories"] += 1
        out_dir = cfg.data_dir / "images" / date_dir / str(story_id)

        try:
            records = _source_story(story, order, http, cfg, out_dir)
        except Exception as exc:  # never let one story crash the run
            logger.error("Image sourcing failed for story %s: %s", story_id, exc)
            records = []

        if not records:
            summary["no_image"] += 1
            logger.warning("No image produced for story %s: %s", story_id, story["headline"][:60])
            continue

        added = 0
        for record in records:
            if db.add_image(record) is not None:
                added += 1
        summary["images_added"] += added

        source_type = records[0].source_type
        summary["by_source"][source_type] = summary["by_source"].get(source_type, 0) + 1
        logger.info("Story %s: %d image(s) via %s", story_id, added, source_type)

    return summary
