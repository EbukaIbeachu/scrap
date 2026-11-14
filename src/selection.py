"""Pick the stories that go into today's video (and remember the choice)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .context import Ctx
from .sections import assign, enabled
from .utils import log


def _matches(story: dict, words: list[str]) -> bool:
    hay = f"{story.get('title', '')} {story.get('summary', '')}".lower()
    return any(w.lower() in hay for w in words)


def _pool(stories: list[dict], cfg_selection: dict) -> list[dict]:
    """Candidates with the keyword filters applied and full-text stories first."""
    include = cfg_selection.get("keywords_include") or []
    exclude = cfg_selection.get("keywords_exclude") or []
    prefer_full = bool(cfg_selection.get("prefer_full_text", True))

    pool = [s for s in stories if not (exclude and _matches(s, exclude))]
    if include:
        pool = [s for s in pool if _matches(s, include)]
    if prefer_full:
        pool = [s for s in pool if s.get("text_quality") == "full"] + [
            s for s in pool if s.get("text_quality") != "full"
        ]
    return pool


def choose_stories(stories: list[dict], cfg_selection: dict) -> list[dict]:
    """Apply keyword filters, per-outlet cap, text preference and top-N. Pure function (testable)."""
    top_n = int(cfg_selection.get("top_n", 5))
    per_outlet = int(cfg_selection.get("max_per_outlet", 2))

    chosen: list[dict] = []
    counts: dict[str, int] = {}
    for s in _pool(stories, cfg_selection):
        if counts.get(s["outlet"], 0) >= per_outlet:
            continue
        chosen.append(s)
        counts[s["outlet"]] = counts.get(s["outlet"], 0) + 1
        if len(chosen) >= top_n:
            break
    return chosen


def select_stories(ctx: Ctx, reselect: bool = False) -> list[dict]:
    """Return today's selected stories. The choice is stored so later stages agree."""
    db = ctx.db
    if not reselect:
        ids = db.get_selection(ctx.date)
        if ids:
            return [s for s in (db.get_story(i) for i in ids) if s]
    hours = int(ctx.get("collector.lookback_hours", 48))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    candidates = db.recent_stories(since)
    cfg_selection = ctx.get("selection", {})
    cfg_sections = ctx.get("sections", {})
    if enabled(cfg_sections):
        groups = assign(_pool(candidates, cfg_selection), cfg_sections)
        chosen = [s for g in groups for s in g["stories"]]
        log.info("Selected %d stories across %d sections for %s: %s", len(chosen), len(groups), ctx.date,
                 ", ".join(f"{g['name']} ({len(g['stories'])})" for g in groups) or "none")
    else:
        chosen = choose_stories(candidates, cfg_selection)
        log.info("Selected %d of %d recent stories for %s", len(chosen), len(candidates), ctx.date)
    db.save_selection(ctx.date, [s["id"] for s in chosen])
    return chosen
