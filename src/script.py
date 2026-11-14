"""Stage 4: turn selected stories into a spoken highlights script (JSON + readable TXT)."""
from __future__ import annotations

import json
from pathlib import Path

from .context import Ctx
from .images.people import usable_people
from .sections import assign, enabled
from .selection import select_stories
from .summarizer import write_script
from .utils import UserError, log


def script_paths(ctx: Ctx) -> tuple[Path, Path]:
    folder = ctx.subdir("scripts")
    return folder / f"{ctx.date}.json", folder / f"{ctx.date}.txt"


def _groups(ctx: Ctx, stories: list[dict]) -> list[dict]:
    """Group the stories into sections, or one anonymous group when sections are off."""
    cfg_sections = ctx.get("sections", {})
    if enabled(cfg_sections):
        return assign(stories, cfg_sections)
    return [{"name": None, "intro": "", "stories": stories}]


def run_script(ctx: Ctx) -> Path:
    """Write (or reuse) today's script.

    Segments: intro, then a section header (when sections are on) followed by its stories,
    then outro.
    """
    json_path, txt_path = script_paths(ctx)
    if json_path.exists() and not ctx.force:
        log.info("Script already exists: %s (use --force to rewrite)", json_path)
        return json_path
    stories = select_stories(ctx)
    if not stories:
        raise UserError("No stories selected. Run:  python main.py collect")
    groups = _groups(ctx, stories)
    for group in groups:
        for story in group["stories"]:
            story["section"] = group["name"]
    result = write_script(ctx, stories)
    written = {item["story_id"]: item for item in result["stories"]}

    segments = [{"kind": "intro", "story_id": None, "section": None,
                 "headline": ctx.get("video.brand.intro_title"),
                 "narration": result["intro"], "source": None, "people": []}]
    for group in groups:
        if group["name"]:
            segments.append({"kind": "section", "story_id": None, "section": group["name"],
                             "headline": group["name"], "narration": group["intro"],
                             "source": None, "people": []})
        for story in group["stories"]:
            item = written[story["id"]]
            people = [{"name": p["name"], "role": p["role"]} for p in usable_people(ctx, story["id"])]
            segments.append({"kind": "story", "story_id": story["id"], "section": group["name"],
                             "headline": item["headline"], "narration": item["narration"],
                             "source": story["outlet"], "people": people, "url": story["url"]})
    segments.append({"kind": "outro", "story_id": None, "section": None,
                     "headline": ctx.get("video.brand.outro_text"),
                     "narration": result["outro"], "source": None, "people": []})

    json_path.write_text(json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"NEWS HIGHLIGHTS SCRIPT - {ctx.date}", ""]
    for i, seg in enumerate(segments):
        if seg["kind"] == "story":
            label = f"STORY {i}: {seg['headline']} ({seg['source']})"
        elif seg["kind"] == "section":
            label = f"SECTION: {seg['section']}"
        else:
            label = seg["kind"].upper()
        lines += [label, seg["narration"], ""]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Script written: %s", txt_path)
    return json_path


def load_script(ctx: Ctx) -> list[dict]:
    json_path, _ = script_paths(ctx)
    if not json_path.exists():
        raise UserError("No script for this date. Run:  python main.py script")
    return json.loads(json_path.read_text(encoding="utf-8"))
