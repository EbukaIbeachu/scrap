"""Claude-powered writing: highlights script, image keywords, public-figure names.

Uses the Messages API over plain ``requests`` (no SDK needed). With ``--dry-run`` every
function falls back to a simple deterministic version so nothing paid is called.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests

from .context import Ctx
from .utils import UserError, log

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class ClaudeLimitError(UserError):
    pass


# --------------------------------------------------------------------------- client
class ClaudeClient:
    """Minimal Messages API client with retries and a per-run call cap."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model = ctx.get("claude.model", "claude-sonnet-5-5")
        self.max_tokens = int(ctx.get("claude.max_tokens", 4000))
        self.temperature = float(ctx.get("claude.temperature", 0.3))
        self.cap = int(ctx.get("general.max_claude_calls_per_run", 20))

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> str:
        if not self.key:
            raise UserError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
                "or run with --dry-run to test without Claude."
            )
        if self.ctx.claude_calls >= self.cap:
            raise ClaudeLimitError(
                f"Claude call cap reached ({self.cap} per run). Raise general.max_claude_calls_per_run "
                "in config.yaml if this is expected."
            )
        body = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {"x-api-key": self.key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        last_err = ""
        for attempt in range(3):
            self.ctx.claude_calls += 1
            try:
                resp = requests.post(API_URL, headers=headers, json=body, timeout=120)
            except requests.RequestException as exc:
                last_err = str(exc)
            else:
                if resp.status_code == 200:
                    blocks = resp.json().get("content", [])
                    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code in (400, 401, 403, 404):
                    break  # retrying will not help
            if self.ctx.claude_calls >= self.cap:
                break
            time.sleep(2 * (attempt + 1))
        raise UserError(f"Claude API request failed. {last_err}")


# --------------------------------------------------------------------------- JSON helpers
def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("No valid JSON found in the model reply")


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


# --------------------------------------------------------------------------- script writing
SCRIPT_SYSTEM = """You are a careful news scriptwriter for a short spoken news-highlights video.

Rules:
- Use ONLY facts that appear in the article text you are given. Never invent details, quotes, numbers, names, places or dates. If an article gives little detail, say less.
- Keep a {tone} tone. Stay neutral; do not add opinions or speculation.
- Write in your own words. Do not copy sentences from the articles.
- Write for the ear: short, clear sentences. No bullet points, markdown, emojis, hashtags or URLs. Spell out symbols (say "percent", "naira", "dollars").
- Attribute information, for example "according to Punch" or "Premium Times reports".
- Write the whole script in this language: {language}.
- Reply with ONLY valid JSON, nothing else."""

STORY_TOPICS = ("player news and transfers, pre-match build-up and team news, interviews and quotes, "
                "major scorelines and results, and football politics (governing-body rulings, club ownership "
                "and takeovers, federation elections, VAR controversies, transfer-window rules)")

SCRIPT_USER = """Write a news highlights script from these {n} stories.

Target length: intro about {intro_words} words; each story about {words} words; outro about {outro_words} words.
Keep the order of the stories. Use natural transitions between stories.

Return JSON in exactly this shape:
{{"intro": "...", "stories": [{{"story_id": <number>, "headline": "short on-screen headline, max 70 characters", "narration": "..."}}], "outro": "..."}}

STORIES:
{stories}"""

SECTION_SCRIPT_USER = """Write a sports news highlights script organised into these sections, in this order: {sections}.

Every story below already belongs to one of those sections. Keep the section order and the story order exactly.
Open each section with a short, natural spoken link into it (do NOT add a separate entry for the section itself).
Target length: intro about {intro_words} words; each story about {words} words; outro about {outro_words} words.
Inside a story, use whatever the article actually contains from: {topics}. If the article has none of those, say less - never pad and never invent.

Return JSON in exactly this shape:
{{"intro": "...", "stories": [{{"story_id": <number>, "headline": "short on-screen headline, max 70 characters", "narration": "..."}}], "outro": "..."}}

STORIES:
{stories}"""


def _stories_block(stories: list[dict], limit: int = 5000) -> str:
    parts = []
    for s in stories:
        note = ""
        if s.get("text_quality") != "full":
            note = "\n(NOTE: only a short summary is available. Keep claims minimal.)"
        section = f"\nSection: {s['section']}" if s.get("section") else ""
        parts.append(
            f"--- STORY {s['id']} ---\nOutlet: {s['outlet']}\nTitle: {s['title']}\n"
            f"Published: {s.get('published') or 'unknown'}{section}{note}\nText: {_clip(s.get('text') or s.get('summary', ''), limit)}"
        )
    return "\n\n".join(parts)


def _headline(text: str, limit: int = 70) -> str:
    """On-screen headline: whole words only, no trailing ellipsis."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")


def mock_script(stories: list[dict], words: int = 60) -> dict:
    """Deterministic placeholder script for --dry-run (extractive; NOT for publishing)."""
    segs = []
    for s in stories:
        body = _clip(s.get("text") or s.get("summary") or s["title"], 600)
        sentences = re.split(r"(?<=[.!?])\s+", body)
        picked, count = [], 0
        for sent in sentences:
            picked.append(sent)
            count += len(sent.split())
            if count >= words:
                break
        narration = f"{s['outlet']} reports. " + " ".join(picked)
        segs.append({"story_id": s["id"], "headline": _headline(s["title"]), "narration": narration})
    return {
        "intro": "Hello, and welcome to today's news highlights.",
        "stories": segs,
        "outro": "That is all for now. Thanks for watching.",
    }


def validate_script(raw: dict, stories: list[dict]) -> dict:
    """Make sure every selected story has a usable narration (fallback to extractive if not)."""
    by_id = {int(s["story_id"]): s for s in raw.get("stories", []) if str(s.get("story_id", "")).lstrip("-").isdigit()}
    fallback = {s["story_id"]: s for s in mock_script(stories)["stories"]}
    fixed = []
    for s in stories:
        item = by_id.get(s["id"]) or {}
        narration = (item.get("narration") or "").strip()
        if len(narration.split()) < 8:
            log.warning("Story %s had no usable narration from Claude; using a plain extract", s["id"])
            item = fallback[s["id"]]
            narration = item["narration"]
        fixed.append({
            "story_id": s["id"],
            "headline": _headline(item.get("headline") or s["title"]),
            "narration": narration,
        })
    return {
        "intro": (raw.get("intro") or "Here are today's news highlights.").strip(),
        "stories": fixed,
        "outro": (raw.get("outro") or "Thanks for watching.").strip(),
    }


def write_script(ctx: Ctx, stories: list[dict]) -> dict:
    """Return {'intro', 'stories': [...], 'outro'} for the selected stories.

    When the stories carry a ``section`` the script is written section by section.
    """
    if ctx.dry_run:
        log.info("Dry run: using a plain extractive script instead of Claude")
        return validate_script(mock_script(stories, int(ctx.get("script.words_per_story", 90)) // 2), stories)
    client = ClaudeClient(ctx)
    system = SCRIPT_SYSTEM.format(tone=ctx.get("script.tone"), language=ctx.get("general.language", "en"))
    common = {
        "n": len(stories),
        "intro_words": ctx.get("script.intro_words", 30),
        "words": ctx.get("script.words_per_story", 90),
        "outro_words": ctx.get("script.outro_words", 25),
        "stories": _stories_block(stories),
        "topics": STORY_TOPICS,
    }
    section_names = list(dict.fromkeys(s["section"] for s in stories if s.get("section")))
    if section_names:
        common["sections"] = ", ".join(section_names)
        user = SECTION_SCRIPT_USER.format(**common)
    else:
        user = SCRIPT_USER.format(**common)
    reply = client.complete(system, user, max_tokens=int(ctx.get("claude.max_tokens", 4000)))
    try:
        raw = extract_json(reply)
        if not isinstance(raw, dict):
            raise ValueError("expected a JSON object")
    except ValueError as exc:
        raise UserError(f"Claude's script reply could not be read ({exc}). Run the script step again.") from exc
    return validate_script(raw, stories)


# --------------------------------------------------------------------------- image keywords
def mock_keywords(story: dict) -> list[str]:
    words = re.findall(r"[A-Za-z]{5,}", story["title"])
    stop = {"about", "after", "their", "which", "would", "could", "there", "where", "being"}
    return [w.lower() for w in words if w.lower() not in stop][:3]


def extract_keywords(ctx: Ctx, stories: list[dict]) -> dict[int, list[str]]:
    """2-3 generic, stock-photo-friendly search keywords per story."""
    if ctx.dry_run:
        return {s["id"]: mock_keywords(s) for s in stories}
    client = ClaudeClient(ctx)
    system = ("You pick stock-photo search keywords for news stories. Choose 2-3 generic, visual keywords "
              "(places, objects, activities), never people's names. Reply with ONLY JSON.")
    user = ('Return {"keywords": {"<story_id>": ["kw1", "kw2"]}} for these stories:\n\n'
            + "\n".join(f"{s['id']}: {s['title']} -- {_clip(s.get('summary', ''), 200)}" for s in stories))
    try:
        data = extract_json(client.complete(system, user, max_tokens=800))
        mapping = data.get("keywords", {})
        return {s["id"]: [str(k) for k in mapping.get(str(s["id"]), [])][:3] or mock_keywords(s) for s in stories}
    except (ValueError, UserError) as exc:
        log.warning("Keyword extraction failed (%s); using headline words", exc)
        return {s["id"]: mock_keywords(s) for s in stories}


# --------------------------------------------------------------------------- public figures
TITLE_RE = re.compile(
    r"\b(President|Vice[- ]President|Governor|Deputy Governor|Senator|Minister|Speaker|Chairman|"
    r"Prime Minister|Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Chief|Alhaji|Pastor)\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})"
)


def mock_people(story: dict, limit: int) -> list[dict]:
    text = f"{story['title']}. {story.get('text') or ''}"
    out, seen = [], set()
    for title, name in TITLE_RE.findall(text):
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "role": title})
        if len(out) >= limit:
            break
    return out


def parse_people_reply(data: Any, stories: list[dict], limit: int) -> dict[int, list[dict]]:
    """Validate Claude's JSON: names must appear in the story text; cap per story."""
    texts = {s["id"]: f"{s['title']} {s.get('text') or ''}".lower() for s in stories}
    result: dict[int, list[dict]] = {s["id"]: [] for s in stories}
    items = data.get("people", []) if isinstance(data, dict) else data
    for item in items or []:
        try:
            sid = int(item.get("story_id"))
            name = re.sub(r"\s+", " ", str(item.get("name", ""))).strip()
        except (TypeError, ValueError, AttributeError):
            continue
        if sid not in texts or len(name.split()) < 2:
            continue
        # The name (or at least its last word) must really be in the article.
        if name.lower() not in texts[sid] and name.split()[-1].lower() not in texts[sid]:
            continue
        if any(p["name"].lower() == name.lower() for p in result[sid]) or len(result[sid]) >= limit:
            continue
        result[sid].append({"name": name, "role": str(item.get("role", "")).strip()})
    return result


def extract_people(ctx: Ctx, stories: list[dict]) -> dict[int, list[dict]]:
    """Public figures named in each story, with their roles. Private individuals are excluded."""
    limit = int(ctx.get("people.max_per_story", 2))
    if ctx.dry_run:
        return {s["id"]: mock_people(s, limit) for s in stories}
    client = ClaudeClient(ctx)
    system = (
        "You extract PUBLIC FIGURES from news articles. Include only well-known people acting in a public "
        "role (politicians, government officials, judges, athletes, entertainers, business leaders) who are "
        "explicitly named in the text. EXCLUDE private individuals, victims, suspects, witnesses, children and "
        "anyone under 18, and anyone not named in the text. The name must appear in the text. "
        "Give the role exactly as the article states it. Reply with ONLY JSON."
    )
    user = (
        f'Return {{"people": [{{"story_id": <number>, "name": "Full Name", "role": "role from the text"}}]}} '
        f"with at most {limit} people per story.\n\n"
        + "\n\n".join(f"--- STORY {s['id']} ---\n{s['title']}\n{_clip(s.get('text') or '', 3000)}" for s in stories)
    )
    try:
        return parse_people_reply(extract_json(client.complete(system, user, max_tokens=1500)), stories, limit)
    except (ValueError, UserError) as exc:
        log.warning("Name extraction failed (%s); no person photos will be used", exc)
        return {s["id"]: [] for s in stories}
