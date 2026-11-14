"""Human-review file: everything you should check before sharing the video."""
from __future__ import annotations

from pathlib import Path

from .context import Ctx
from .images.people import is_usable
from .script import load_script, script_paths
from .utils import log


def write_review(ctx: Ctx) -> Path:
    """Write data/output/review_<date>.md with headlines, sources, narration and person photos."""
    out = ctx.subdir("output") / f"review_{ctx.date}.md"
    json_path, _ = script_paths(ctx)
    segments = load_script(ctx) if json_path.exists() else []
    db = ctx.db
    lines = [
        f"# Review before sharing - {ctx.date}",
        "",
        "Check every item below before you send the video to anyone.",
        "",
        "- Compare each narration with the original article (AI summaries can contain mistakes).",
        "- Confirm every person photo really shows the person named.",
        "- News photos and text are usually copyrighted. Only share what you have the right to share.",
        "",
    ]
    if ctx.dry_run:
        lines += ["> DRY RUN: the narration below is a plain extract, and the voice is a silent placeholder.", ""]
    for i, seg in enumerate(segments):
        if seg["kind"] != "story":
            lines += [f"## {seg.get('section') or seg['kind'].title()}", seg["narration"], ""]
            continue
        story = db.get_story(seg["story_id"]) or {}
        lines += [f"## Story {i}: {seg['headline']}", "",
                  f"- Outlet: {seg['source']}", f"- Link: {seg.get('url') or story.get('url', '')}",
                  f"- Published: {story.get('published') or 'unknown'}"]
        if story.get("text_quality") != "full":
            lines.append("- WARNING: only a short summary was available for this story. Check the claims carefully.")
        lines += ["", "**Narration:**", "", seg["narration"], "", "**Images:**", ""]
        for img in db.images_for(seg["story_id"]):
            lines.append(f"- {img['kind']}: {Path(img['path']).name} | {img.get('license_note') or 'no licence note'}"
                         + (f" | source: {img['source_url']}" if img.get("source_url") else ""))
        people = db.people_for(seg["story_id"])
        if people:
            lines += ["", "**Public figures:**", ""]
            for p in people:
                if not p.get("path"):
                    lines.append(f"- {p['name']} ({p.get('role') or 'role not stated'}): no safe photo found - {p.get('note') or ''}")
                    continue
                state = {1: "APPROVED", 0: "REJECTED", None: "not approved"}[p.get("approved")]
                used = "USED in video" if is_usable(ctx, p) else "not used in video"
                lines.append(
                    f"- {p['name']} ({p.get('role') or 'role not stated'}): {state}, {used}. "
                    f"Source: {p['source_url']} | Licence: {p['license']} | Credit: {p['credit']} | "
                    f"Confidence: {p['confidence']:.2f}"
                )
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("Review file: %s", out)
    return out
