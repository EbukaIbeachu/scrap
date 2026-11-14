"""Named video sections: rule-based routing of stories into an ordered set of sections.

With ``sections.enabled: true`` in config.yaml the video becomes
``intro, [section header, story...]*, outro`` instead of a flat ``intro, story*, outro``.

Routing is deterministic (the first rule whose keyword appears in the story wins), so every
later stage can re-derive a story's section from the config and nothing extra has to be
stored in the database. A keyword in the headline counts for more than one that only turns
up in the summary, so a story is not filed under a club it merely mentions in passing.
"""
from __future__ import annotations

from .utils import log

DEFAULT_HEADER_TEMPLATE = "Now, {name}."


def parse_rules(cfg_sections: dict) -> list[dict]:
    """Ordered, validated section rules. Rules without a name or keywords are ignored."""
    rules: list[dict] = []
    for raw in (cfg_sections or {}).get("rules") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        keywords = [str(k).strip() for k in (raw.get("keywords") or []) if str(k).strip()]
        if not name or not keywords:
            log.warning("Ignoring a section rule with no name or no keywords: %r", raw)
            continue
        rules.append({
            "name": name,
            "keywords": keywords,
            "limit": max(int(raw.get("limit", 1) or 1), 1),
            "intro": str(raw.get("intro") or "").strip(),
        })
    return rules


def enabled(cfg_sections: dict) -> bool:
    """True when sections are switched on AND at least one usable rule exists."""
    return bool((cfg_sections or {}).get("enabled", False)) and bool(parse_rules(cfg_sections))


def _match(rules: list[dict], hay: str) -> str | None:
    """Name of the first rule with a keyword in ``hay``; None when nothing matches."""
    hay = hay.lower()
    for rule in rules:
        if any(word.lower() in hay for word in rule["keywords"]):
            return rule["name"]
    return None


def _haystacks(story: dict) -> tuple[str, str]:
    """Headline, then headline + summary. Filled in that order, a headline match wins."""
    title = str(story.get("title") or "").lower()
    return title, f"{title} {story.get('summary') or ''}".lower()


def section_of(story: dict, rules: list[dict]) -> str | None:
    """Name of the first rule matching the story; None when nothing matches."""
    for hay in _haystacks(story):
        name = _match(rules, hay)
        if name:
            return name
    return None


def assign(stories: list[dict], cfg_sections: dict) -> list[dict]:
    """Group stories into sections, in rule order, at most ``limit`` stories per section.

    Headline matches are placed first, then stories that only mention a keyword in their
    summary fill whatever room is left. Returns ``[{"name", "intro", "stories": [...]}]``
    for the sections that actually got a story, so a quiet day yields fewer sections.
    Stories matching no rule are dropped.
    """
    rules = parse_rules(cfg_sections)
    template = str((cfg_sections or {}).get("header_template") or DEFAULT_HEADER_TEMPLATE)
    buckets: dict[str, list[dict]] = {rule["name"]: [] for rule in rules}
    placed: set[int] = set()
    for depth in (0, 1):
        for i, story in enumerate(stories):
            if i in placed:
                continue
            name = _match(rules, _haystacks(story)[depth])
            if name is None:
                continue
            rule = next(r for r in rules if r["name"] == name)
            if len(buckets[name]) >= rule["limit"]:
                continue
            buckets[name].append(story)
            placed.add(i)

    groups = []
    for rule in rules:
        picked = buckets[rule["name"]]
        if not picked:
            continue
        groups.append({
            "name": rule["name"],
            "intro": rule["intro"] or template.format(name=rule["name"]),
            "stories": picked,
        })
    return groups
