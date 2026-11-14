"""Stage 3: photos of PUBLIC FIGURES named in the stories.

Identity comes only from names in the article text. There is no facial recognition anywhere.
Automatic lookup uses Wikipedia/Wikimedia Commons (openly licensed images only). You can
also drop your own photos in ``data/people_photos/`` named like ``Firstname Lastname.jpg``.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

from ..context import Ctx
from ..selection import select_stories
from ..summarizer import extract_people
from ..utils import ask_yes_no, is_interactive, log, open_path, slugify, strip_accents

ROLE_STOPWORDS = {"the", "of", "and", "for", "state", "federal", "former", "current", "new", "chief", "general"}
FREE_LICENSE_RE = re.compile(r"^(cc0|public domain|pd[- ]|cc[- ]by)", re.I)


# --------------------------------------------------------------------------- scoring (pure, testable)
def normalize_name(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # drop "(politician)"
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def role_keywords(role: str) -> set[str]:
    return {w for w in normalize_name(role).split() if len(w) >= 4 and w not in ROLE_STOPWORDS}


def score_candidate(name: str, role: str, title: str, extract: str, is_disambiguation: bool) -> float:
    """Confidence (0..1) that a Wikipedia page is about this named person."""
    if is_disambiguation:
        return 0.0
    name_n, title_n = normalize_name(name), normalize_name(title)
    exact = title_n == name_n
    contains = all(tok in title_n.split() for tok in name_n.split())
    keys = role_keywords(role)
    role_match = bool(keys) and any(k in extract.lower() for k in keys)
    if exact and role_match:
        return 0.9
    if exact:
        return 0.7
    if contains and role_match:
        return 0.6
    return 0.0


def pick_candidate(name: str, role: str, pages: list[dict], minimum: float = 0.6) -> tuple[dict | None, float, str]:
    """Choose the page for a person, rejecting ambiguous results (several people, same name)."""
    scored = []
    for p in pages:
        s = score_candidate(name, role, p["title"], p.get("extract", ""), p.get("disambiguation", False))
        if s >= minimum:
            scored.append((s, p))
    if not scored:
        return None, 0.0, "no page matched the name and role"
    scored.sort(key=lambda x: -x[0])
    best = scored[0]
    # Two different pages that match equally well (e.g. two senators called John Smith) -> do not guess.
    if len(scored) > 1 and scored[1][0] >= best[0] - 0.05:
        return None, 0.0, "ambiguous: several people match this name"
    return best[1], best[0], "matched"


def license_ok(short_name: str) -> tuple[bool, bool]:
    """(usable for personal use, usable commercially) for a Commons license name."""
    s = (short_name or "").strip().lower()
    if not s or "fair use" in s or "non-free" in s:
        return False, False
    if not FREE_LICENSE_RE.match(s):
        return False, False
    commercial = not re.search(r"\b(nc|nd)\b|-nc|-nd", s)
    return True, commercial


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


# --------------------------------------------------------------------------- Wikipedia / Commons
class WikiClient:
    def __init__(self, ctx: Ctx):
        self.lang = ctx.get("people.wikipedia_lang", "en")
        self.timeout = float(ctx.get("general.request_timeout", 20))
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": ctx.get("general.user_agent")})

    def _get(self, url: str, params: dict) -> dict:
        time.sleep(0.4)
        resp = self.s.get(url, params={**params, "format": "json"}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def candidates(self, name: str) -> list[dict]:
        api = f"https://{self.lang}.wikipedia.org/w/api.php"
        found = self._get(api, {"action": "query", "list": "search", "srsearch": name, "srlimit": 5})
        ids = [str(r["pageid"]) for r in found.get("query", {}).get("search", [])]
        if not ids:
            return []
        info = self._get(api, {
            "action": "query", "pageids": "|".join(ids), "prop": "pageimages|pageprops|extracts",
            "piprop": "name", "ppprop": "disambiguation", "exintro": 1, "explaintext": 1, "exchars": 500,
        })
        pages = []
        for p in info.get("query", {}).get("pages", {}).values():
            pages.append({
                "title": p.get("title", ""),
                "extract": p.get("extract", ""),
                "disambiguation": "disambiguation" in (p.get("pageprops") or {}),
                "pageimage": p.get("pageimage"),
                "url": f"https://{self.lang}.wikipedia.org/wiki/{p.get('title', '').replace(' ', '_')}",
            })
        return pages

    def commons_file(self, filename: str) -> dict | None:
        data = self._get("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime", "iiurlwidth": 1280,
        })
        for page in data.get("query", {}).get("pages", {}).values():
            infos = page.get("imageinfo")
            if not infos:
                return None  # not on Commons (e.g. a non-free local file): do not use
            info = infos[0]
            meta = info.get("extmetadata", {})
            return {
                "url": info.get("thumburl") or info.get("url"),
                "page": info.get("descriptionurl", ""),
                "license": (meta.get("LicenseShortName") or {}).get("value", ""),
                "artist": strip_html((meta.get("Artist") or {}).get("value", "")),
                "mime": info.get("mime", ""),
            }
        return None

    def download(self, url: str) -> bytes | None:
        try:
            time.sleep(0.4)
            resp = self.s.get(url, timeout=self.timeout)
            return resp.content if resp.status_code == 200 else None
        except requests.RequestException:
            return None


def save_person_photo(data: bytes | Path, dest: Path) -> bool:
    """Save a (not cropped) person photo as RGB JPEG, max 1600px. False if unreadable or tiny."""
    try:
        img = Image.open(io.BytesIO(data) if isinstance(data, bytes) else data)
        img = ImageOps.exif_transpose(img).convert("RGB")
        if min(img.size) < 200:
            return False
        img.thumbnail((1600, 1600))
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=90)
        return True
    except Exception:
        return False


def lookup_wikipedia(ctx: Ctx, wiki: WikiClient, name: str, role: str, dest: Path) -> dict:
    pages = wiki.candidates(name)
    page, conf, why = pick_candidate(name, role, pages)
    if page is None:
        return {"note": why}
    if not page.get("pageimage"):
        return {"note": f"page '{page['title']}' has no image"}
    f = wiki.commons_file(page["pageimage"])
    if not f or not f.get("url"):
        return {"note": "image is not on Wikimedia Commons (possibly non-free); not used"}
    personal_ok, commercial_ok = license_ok(f["license"])
    if not personal_ok:
        return {"note": f"license '{f['license']}' not accepted"}
    data = wiki.download(f["url"])
    if not data or not save_person_photo(data, dest):
        return {"note": "photo could not be downloaded"}
    return {
        "path": str(dest), "source_url": f["page"] or page["url"], "license": f["license"],
        "credit": f"Photo: {f['artist'] or 'Unknown'} / Wikimedia Commons ({f['license']})",
        "confidence": conf, "commercial_ok": int(commercial_ok), "note": f"matched Wikipedia page '{page['title']}'",
    }


def lookup_manual(ctx: Ctx, name: str, dest: Path) -> dict | None:
    folder = ctx.resolve_path(ctx.get("people.manual_photos_dir", "people_photos"))
    if not folder.exists():
        return None
    want = slugify(name)
    for f in folder.iterdir():
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and slugify(f.stem) == want:
            if save_person_photo(f, dest):
                return {
                    "path": str(dest), "source_url": str(f), "license": "supplied by you",
                    "credit": "Photo: supplied by the producer", "confidence": 1.0,
                    "commercial_ok": int(bool(ctx.get("people.manual_commercial_ok", False))),
                    "note": "your own photo", "approved": True,
                }
    return None


def lookup_search_api(ctx: Ctx, name: str, role: str, dest: Path) -> dict | None:
    """Optional Google Programmable Search image lookup. License is UNKNOWN, so never auto-used."""
    conf = ctx.get("people.search_api", {})
    key, cx = os.getenv(conf.get("key_env", "")), os.getenv(conf.get("cx_env", ""))
    if not (conf.get("enabled") and key and cx):
        return None
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": key, "cx": cx, "q": f"{name} {role}", "searchType": "image", "num": 3},
            timeout=float(ctx.get("general.request_timeout", 20)),
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            data = requests.get(item["link"], timeout=20, headers={"User-Agent": ctx.get("general.user_agent")}).content
            if save_person_photo(data, dest):
                return {
                    "path": str(dest), "source_url": item.get("image", {}).get("contextLink", item["link"]),
                    "license": "unknown - verify before sharing", "credit": f"Photo: {item.get('displayLink', 'web')}",
                    "confidence": 0.4, "commercial_ok": 0, "note": "from image search; check it is the right person",
                }
    except Exception as exc:
        log.info("  image-search lookup failed: %s", exc)
    return None


# --------------------------------------------------------------------------- usage rules
def is_usable(ctx: Ctx, person: dict) -> bool:
    """Should this person's photo appear in the video?"""
    if not person.get("path") or not Path(person["path"]).exists():
        return False
    if person.get("approved") == 0:
        return False
    if ctx.monetization_safe and not person.get("commercial_ok"):
        return False
    if person.get("approved") == 1:
        return True
    if ctx.get("people.require_approval", True):
        return False
    return float(person.get("confidence") or 0) >= float(ctx.get("people.auto_use_min_confidence", 0.85))


def usable_people(ctx: Ctx, story_id: int) -> list[dict]:
    return [p for p in ctx.db.people_for(story_id) if is_usable(ctx, p)]


# --------------------------------------------------------------------------- stage
def run_people(ctx: Ctx) -> int:
    """Find photos of public figures named in the selected stories. Idempotent."""
    if not ctx.get("people.enabled", True):
        log.info("Public-figure photos are disabled (people.enabled: false)")
        return 0
    stories = select_stories(ctx)
    if not stories:
        return 0
    cache = ctx.subdir("people") / f"{ctx.date}.json"
    names: dict[int, list[dict]]
    if cache.exists() and not ctx.force:
        names = {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
    else:
        names = extract_people(ctx, stories)
        cache.write_text(json.dumps(names, indent=2), encoding="utf-8")

    wiki = WikiClient(ctx)
    found = 0
    for story in stories:
        for person in names.get(story["id"], []):
            name, role = person["name"], person.get("role", "")
            if ctx.db.person_known(story["id"], name) and not ctx.force:
                continue
            dest = ctx.subdir("images", ctx.date, str(story["id"])) / f"person_{slugify(name)}.jpg"
            result: dict = {}
            try:
                result = lookup_manual(ctx, name, dest) or {}
                if not result:
                    result = lookup_wikipedia(ctx, wiki, name, role, dest)
                if not result.get("path"):
                    extra = lookup_search_api(ctx, name, role, dest)
                    if extra:
                        result = extra
            except Exception as exc:  # network trouble must not stop the run
                result = {"note": f"lookup failed: {exc}"}
            approved = result.pop("approved", None)
            ctx.db.upsert_person(story["id"], name, role, **result)
            if approved is not None:
                row = next(p for p in ctx.db.people_for(story["id"]) if p["name"] == name)
                ctx.db.set_person_approved(row["id"], approved)
            if result.get("path"):
                found += 1
                log.info("  photo found: %s (%s, confidence %.2f)", name, role or "no role", result.get("confidence", 0))
            else:
                log.info("  no safe photo for %s: %s", name, result.get("note", ""))
    log.info("Person photos found: %d", found)
    return found


def approve_people(ctx: Ctx, approve_all: bool = False) -> int:
    """Ask the user to confirm each found face matches the name. Returns the number decided."""
    stories = select_stories(ctx)
    pending = ctx.db.pending_people([s["id"] for s in stories])
    if not pending:
        log.info("No person photos are waiting for approval.")
        return 0
    if not approve_all and not is_interactive():
        log.info("%d person photo(s) need your approval. Run: python main.py approve-people", len(pending))
        return 0
    decided = 0
    for p in pending:
        if approve_all:
            ctx.db.set_person_approved(p["id"], True)
            decided += 1
            continue
        print("\n" + "-" * 60)
        print(f"Name:       {p['name']}   ({p['role'] or 'role not stated'})")
        print(f"Photo file: {p['path']}")
        print(f"Source:     {p['source_url']}")
        print(f"License:    {p['license']}   Confidence: {p['confidence']:.2f}")
        print(f"Credit:     {p['credit']}")
        while True:
            ans = input("Is this really that person? [y]es / [n]o / [o]pen the photo / [s]kip for now: ").strip().lower()
            if ans in ("o", "open"):
                open_path(Path(p["path"]))
                continue
            break
        if ans in ("y", "yes"):
            ctx.db.set_person_approved(p["id"], True)
            decided += 1
        elif ans in ("n", "no"):
            ctx.db.set_person_approved(p["id"], False)
            decided += 1
    log.info("Decided %d of %d person photos", decided, len(pending))
    return decided
