"""Fast unit tests: database, RSS parsing, selection, sections, text prep, captions, export planning, people logic."""
import io
import json
import sys

import pytest
from PIL import Image

from src.collector.article import extract_article
from src.collector.rss import parse_date, parse_feed
from src.config import DEFAULTS, deep_merge
from src.context import Ctx
from src.db import Database
from src.images.article_images import check_image, fit_image
from src.images.people import (license_ok, normalize_name, pick_candidate, role_keywords, score_candidate)
from src.sections import assign, enabled, parse_rules, section_of
from src.selection import choose_stories
from src.summarizer import extract_json, mock_people, parse_people_reply, validate_script
from src.tts.text_prep import (group_sentences, int_to_words, number_to_words, prepare_text, split_sentences,
                               year_to_words)
from src.video.captions import chunk_timings, split_caption_chunks
from src.video.export import plan_export, split_segments, video_kbps_for
from src.utils import load_font

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Test</title>
<item><title>First &amp; best</title><link>https://ex.test/a</link>
<description>&lt;p&gt;Short &lt;b&gt;summary&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Fri, 13 Nov 2026 10:00:00 +0100</pubDate>
<media:content url="https://ex.test/a.jpg" medium="image"/>
<content:encoded><![CDATA[<p>Body text.</p><img src="https://ex.test/inline.jpg">]]></content:encoded></item>
<item><title>No link</title></item>
<item><title>Second</title><link>https://ex.test/b</link><enclosure url="https://ex.test/b.png" type="image/png"/></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom one</title>
<link rel="alternate" href="https://ex.test/atom"/><updated>2026-11-13T09:00:00Z</updated>
<summary>Atom summary</summary></entry></feed>"""


# ------------------------------------------------------------------ database
def test_db_unique_url_and_selection():
    db = Database(":memory:")
    sid = db.add_story("O", "https://x/1", "T", text="body", text_quality="full", image_urls=["a", "a", "b"])
    assert sid and db.add_story("O", "https://x/1", "T again") is None  # duplicate URL ignored
    assert db.story_exists("https://x/1") and db.count_stories() == 1
    assert db.get_story(sid)["image_urls"] == ["a", "b"]
    db.save_selection("2026-11-13", [sid])
    assert db.get_selection("2026-11-13") == [sid]
    db.add_image(sid, "card", "/tmp/c.jpg", license_note="x")
    assert len(db.images_for(sid)) == 1
    db.delete_images(sid)
    assert db.images_for(sid) == []


def test_db_people_keep_approval_on_update():
    db = Database(":memory:")
    sid = db.add_story("O", "https://x/1", "T")
    db.upsert_person(sid, "Jane Doe", "Governor", path="/p.jpg", confidence=0.9)
    pid = db.people_for(sid)[0]["id"]
    assert len(db.pending_people([sid])) == 1
    db.set_person_approved(pid, True)
    db.upsert_person(sid, "Jane Doe", "Governor of X", path="/p2.jpg")
    row = db.people_for(sid)[0]
    assert row["approved"] == 1 and row["role"] == "Governor of X" and row["path"] == "/p2.jpg"
    assert db.pending_people([sid]) == []


# ------------------------------------------------------------------ rss / article
def test_parse_rss():
    entries = parse_feed(RSS)
    assert [e.title for e in entries] == ["First & best", "Second"]  # item without a link is dropped
    first = entries[0]
    assert first.summary == "Short summary" and first.content == "Body text."
    assert first.published == "2026-11-13T09:00:00+00:00"
    assert first.image_urls[:2] == ["https://ex.test/a.jpg", "https://ex.test/inline.jpg"]
    assert entries[1].image_urls == ["https://ex.test/b.png"]


def test_parse_atom_and_dates():
    e = parse_feed(ATOM)[0]
    assert e.link == "https://ex.test/atom" and e.published == "2026-11-13T09:00:00+00:00"
    assert parse_date("garbage") is None and parse_date(None) is None


def test_extract_article_fallback(monkeypatch):
    html = """<html><head><meta property="og:image" content="/img/top.jpg"></head><body>
    <nav><p>Menu item that is long enough to pass the filter for sure yes</p></nav>
    <article><p>This is the first real paragraph of the article, long enough to keep.</p>
    <p>short</p><p>The second real paragraph also has plenty of characters in it.</p></article></body></html>"""
    monkeypatch.setitem(sys.modules, "trafilatura", None)  # force the built-in extractor
    art = extract_article(html, "https://ex.test/news/1")
    assert art.image_url == "https://ex.test/img/top.jpg"
    assert "first real paragraph" in art.text and "short" not in art.text and "Menu" not in art.text


# ------------------------------------------------------------------ config / selection
def test_deep_merge_keeps_defaults():
    cfg = deep_merge(DEFAULTS, {"tts": {"speed": 1.2}})
    assert cfg["tts"]["speed"] == 1.2 and cfg["tts"]["edge_tts"]["voice"] == "en-NG-EzinneNeural"


def _story(i, outlet, title, quality="full"):
    return {"id": i, "outlet": outlet, "title": title, "summary": "", "text_quality": quality}


def test_choose_stories_filters_caps_and_prefers_full():
    stories = [_story(1, "A", "Lagos floods", "summary_only"), _story(2, "A", "Economy grows"),
               _story(3, "A", "Sports win"), _story(4, "B", "Celebrity gossip"), _story(5, "B", "Election news")]
    out = choose_stories(stories, {"top_n": 4, "max_per_outlet": 2, "keywords_exclude": ["gossip"]})
    assert [s["id"] for s in out] == [2, 3, 5]  # full text first; outlet A capped at 2; gossip excluded
    out = choose_stories(stories, {"top_n": 4, "max_per_outlet": 3, "keywords_exclude": ["gossip"]})
    assert [s["id"] for s in out] == [2, 3, 5, 1]  # summary-only story only fills a remaining slot
    assert [s["id"] for s in choose_stories(stories, {"top_n": 5, "max_per_outlet": 9, "keywords_include": ["lagos"]})] == [1]


# ------------------------------------------------------------------ sections
CFG_SECTIONS = {
    "enabled": True,
    "header_template": "Now, {name}.",
    "rules": [
        {"name": "Real Madrid FC", "keywords": ["Real Madrid"], "limit": 1},
        {"name": "La Liga", "keywords": ["La Liga", "Barcelona"], "limit": 1, "intro": "Next, La Liga."},
        {"name": "English Premiership", "keywords": ["Premier League", "Arsenal"], "limit": 1},
        {"name": "Serie A", "keywords": ["Serie A", "Juventus"], "limit": 1},
    ],
}


def _section_story(i, title, quality="full"):
    return {"id": i, "outlet": "Wire", "title": title, "summary": "", "text_quality": quality}


def test_sections_are_off_by_default_and_need_rules():
    assert not enabled({})
    assert not enabled({"enabled": True, "rules": []})
    assert not enabled({"enabled": True, "rules": [{"name": "No keywords", "keywords": []}]})
    assert not enabled({"enabled": False, "rules": CFG_SECTIONS["rules"]})
    assert enabled(CFG_SECTIONS)


def test_section_routing_first_match_wins():
    rules = parse_rules(CFG_SECTIONS)
    # a Real Madrid story is also La Liga, but Real Madrid FC comes first
    assert section_of(_section_story(1, "Real Madrid beat Barcelona in La Liga"), rules) == "Real Madrid FC"
    assert section_of(_section_story(2, "Barcelona held by Sevilla in La Liga"), rules) == "La Liga"
    assert section_of(_section_story(3, "Arsenal go top of the Premier League"), rules) == "English Premiership"
    assert section_of(_section_story(4, "Juventus close on the Serie A title"), rules) == "Serie A"
    assert section_of(_section_story(5, "Djokovic wins the Australian Open"), rules) is None
    # matching only looks at the title and the summary, and ignores case
    assert section_of({"title": "Transfer round-up", "summary": "real madrid agree a fee"}, rules) == "Real Madrid FC"


def test_headline_matches_outrank_summary_mentions():
    rules = parse_rules(CFG_SECTIONS)
    # a story that only drops "Barcelona" in its summary is not really a La Liga story
    passing = {"title": "Transfer round-up", "summary": "Barcelona agree a fee."}
    assert section_of(passing, rules) == "La Liga"  # the last resort still matches
    real = _section_story(2, "Barcelona held by Sevilla in La Liga")
    groups = assign([passing, real], CFG_SECTIONS)
    # but the headline match takes the one La Liga slot
    assert [s["id"] for s in groups[0]["stories"]] == [2]
    # a summary mention still fills a section that no headline match covers
    only_summary = {"title": "Transfer round-up", "summary": "Juventus agree a fee."}
    assert [s["title"] for s in assign([only_summary], CFG_SECTIONS)[0]["stories"]] == ["Transfer round-up"]


def test_sections_order_caps_and_headers():
    stories = [_section_story(1, "Djokovic wins the Australian Open"),
               _section_story(2, "Juventus close on the Serie A title"),
               _section_story(3, "Arsenal go top of the Premier League"),
               _section_story(4, "Real Madrid beat Barcelona"),
               _section_story(5, "Barcelona held by Sevilla"),
               _section_story(6, "Arsenal sign a new striker")]
    groups = assign(stories, CFG_SECTIONS)
    assert [g["name"] for g in groups] == ["Real Madrid FC", "La Liga", "English Premiership", "Serie A"]
    assert [[s["id"] for s in g["stories"]] for g in groups] == [[4], [5], [3], [2]]  # 6 dropped by the cap
    assert groups[0]["intro"] == "Now, Real Madrid FC."  # from the template
    assert groups[1]["intro"] == "Next, La Liga."  # the rule overrides it
    # a section with no stories disappears entirely instead of becoming an empty header
    assert assign([stories[1]], CFG_SECTIONS) == [
        {"name": "Serie A", "intro": "Now, Serie A.", "stories": [stories[1]]}]


def test_script_writes_a_header_before_each_section(tmp_path):
    from src.script import run_script

    cfg = deep_merge(DEFAULTS, {
        "general": {"data_dir": str(tmp_path / "data")},
        "sections": CFG_SECTIONS,
    })
    ctx = Ctx(cfg=cfg, root=tmp_path, date="2026-11-13", dry_run=True)
    ids = [ctx.db.add_story("Wire", f"https://ex.test/{i}", title, text="Body text. " * 40, text_quality="full")
           for i, title in enumerate(["Real Madrid beat Barcelona", "Arsenal go top of the Premier League"], start=1)]
    ctx.db.save_selection("2026-11-13", ids)

    run_script(ctx)
    script = json.loads((ctx.data_dir / "scripts" / "2026-11-13.json").read_text(encoding="utf-8"))
    assert [s["kind"] for s in script] == ["intro", "section", "story", "section", "story", "outro"]
    assert [s["section"] for s in script if s["kind"] == "section"] == ["Real Madrid FC", "English Premiership"]
    assert script[1]["narration"] == "Now, Real Madrid FC."
    assert script[1]["headline"] == "Real Madrid FC"
    assert all(s["section"] is None for s in script if s["kind"] in ("intro", "outro"))


# ------------------------------------------------------------------ images
def test_fit_image_exact_size_and_check(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (800, 300), (10, 120, 200)).save(buf, "PNG")
    out = fit_image(buf.getvalue(), tmp_path / "_fit_test.jpg", (540, 960))
    assert Image.open(out).size == (540, 960)
    assert check_image(buf.getvalue(), 500, 300)[0]
    assert not check_image(buf.getvalue(), 900, 300)[0]
    assert not check_image(b"not an image", 10, 10)[0]


# ------------------------------------------------------------------ summarizer helpers
def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": [1, 2]} thanks') == {"a": [1, 2]}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_parse_people_reply_checks_names_against_text():
    stories = [{"id": 1, "title": "Governor Jane Doe opens road", "text": "Jane Doe said the road is ready."},
               {"id": 2, "title": "Other", "text": "Nothing here."}]
    data = {"people": [
        {"story_id": 1, "name": "Jane Doe", "role": "Governor of X State"},
        {"story_id": 1, "name": "jane doe", "role": "dup"},                 # duplicate
        {"story_id": 1, "name": "Invented Person", "role": "Minister"},       # not in the text
        {"story_id": 1, "name": "Madonna", "role": "Singer"},                 # single word
        {"story_id": 2, "name": "Jane Doe", "role": "Governor"},              # wrong story
        {"story_id": "x", "name": "Bad Row", "role": ""},
    ]}
    out = parse_people_reply(data, stories, limit=2)
    assert out[1] == [{"name": "Jane Doe", "role": "Governor of X State"}] and out[2] == []


def test_mock_people_and_validate_script():
    story = {"id": 1, "title": "x", "text": "President Bola Tinubu and Governor Babajide Sanwo Olu met."}
    assert [p["name"] for p in mock_people(story, 5)][0] == "Bola Tinubu"
    stories = [{"id": 1, "outlet": "O", "title": "Title one", "text": "A long enough body. " * 20, "summary": ""}]
    fixed = validate_script({"stories": [{"story_id": 1, "narration": "too short"}]}, stories)
    assert len(fixed["stories"][0]["narration"].split()) >= 8  # fell back to an extract
    assert fixed["intro"] and fixed["outro"]


# ------------------------------------------------------------------ people matching
def test_people_scoring_and_ambiguity():
    assert normalize_name("Bola Tinubu (politician)") == "bola tinubu"
    assert "governor" in role_keywords("Governor of Lagos State") and "state" not in role_keywords("Governor of Lagos State")
    assert score_candidate("Jane Doe", "Governor", "Jane Doe", "Jane Doe is a governor.", False) == 0.9
    assert score_candidate("Jane Doe", "Governor", "Jane Doe", "A singer.", False) == 0.7
    assert score_candidate("Jane Doe", "Governor", "Jane Mary Doe", "She is a governor.", False) == 0.6
    assert score_candidate("Jane Doe", "Governor", "Jane Doe", "governor", True) == 0.0
    assert score_candidate("Jane Doe", "Governor", "Doe Jane Foundation", "nothing", False) == 0.0
    pages = [{"title": "John Smith (politician)", "extract": "A politician and senator."},
             {"title": "John Smith (footballer)", "extract": "A football player."}]
    page, conf, _ = pick_candidate("John Smith", "Senator", pages)
    assert page["title"] == "John Smith (politician)" and conf == 0.9
    same = [{"title": "John Smith (politician)", "extract": "A senator."}, {"title": "John Smith (actor)", "extract": "A senator too."}]
    assert pick_candidate("John Smith", "Senator", same)[0] is None  # ambiguous
    assert pick_candidate("Nobody Here", "Mayor", pages)[0] is None


def test_license_rules():
    assert license_ok("CC BY-SA 4.0") == (True, True)
    assert license_ok("CC0") == (True, True)
    assert license_ok("Public domain") == (True, True)
    assert license_ok("CC BY-NC 4.0") == (True, False)
    assert license_ok("CC BY-ND 2.0") == (True, False)
    assert license_ok("Fair use") == (False, False)
    assert license_ok("") == (False, False) and license_ok("All rights reserved") == (False, False)


# ------------------------------------------------------------------ text prep
def test_numbers_and_years():
    assert int_to_words(0) == "zero" and int_to_words(21) == "twenty-one"
    assert int_to_words(1250) == "one thousand, two hundred and fifty"
    assert int_to_words(2_000_000) == "two million"
    assert number_to_words("3.5") == "three point five"
    assert year_to_words(2026) == "twenty twenty-six" and year_to_words(1999) == "nineteen ninety-nine"
    assert year_to_words(2005) == "two thousand and five" and year_to_words(1905) == "nineteen oh five"


def test_prepare_text():
    t = prepare_text("The govt spent ₦5 billion, or $2.5m, in March 2026. Inflation hit 24% (see https://x.co).",
                     {"NNPC": "N N P C"})
    assert "five billion naira" in t and "two point five million dollars" in t
    assert "March twenty twenty-six" in t and "twenty-four percent" in t
    assert "government" in t and "http" not in t
    assert prepare_text("NNPC reported", {"NNPC": "N N P C"}) == "N N P C reported"
    assert prepare_text("Dr. Ade met 300 people.") == "Doctor Ade met three hundred people."


def test_sentence_split_and_grouping():
    s = split_sentences("First one. Second one! Is this third? Yes.")
    assert s == ["First one.", "Second one!", "Is this third?", "Yes."]
    groups = group_sentences(["Hi.", "This is a much longer second sentence here.", "Ok."], min_chars=20, max_chars=200)
    assert all(len(g) >= 3 for g in groups) and " ".join(groups).replace("  ", " ") == "Hi. This is a much longer second sentence here. Ok."
    long_groups = group_sentences(["word " * 10 + "end."] * 4, max_chars=120)
    assert len(long_groups) == 4 and all(len(g) <= 120 for g in long_groups)


# ------------------------------------------------------------------ captions
def test_caption_chunks_fit_and_timings():
    font = load_font(30, bold=True)
    text = "The quick brown fox jumps over the lazy dog while the news anchor reads the latest headlines aloud. " * 3
    chunks = split_caption_chunks(text, font, 400, max_lines=2)
    assert len(chunks) > 2 and " ".join(chunks).split() == text.split()
    times = chunk_timings(chunks, 12.0)
    assert times[0][0] == pytest.approx(0.1) and times[-1][1] == pytest.approx(12.0, abs=0.01)
    assert all(a[1] == pytest.approx(b[0], abs=0.01) for a, b in zip(times, times[1:]))
    assert chunk_timings([], 5.0) == []


# ------------------------------------------------------------------ export planning
def test_export_plan_single_part():
    parts = plan_export([10.0, 40.0, 40.0, 40.0, 10.0], target_mb=15, audio_kbps=96, min_kbps=350, max_kbps=2500)
    assert len(parts) == 1 and parts[0]["segments"] == [0, 1, 2, 3, 4]
    est_mb = (parts[0]["video_kbps"] + 96) * 1000 / 8 * 140 / 1_000_000
    assert est_mb < 15  # the planned bitrate keeps it under the limit


def test_export_plan_splits_long_video():
    durations = [20.0] * 40  # 800 seconds: too long for 15 MB at >= 350 kbps
    parts = plan_export(durations, 15, 96, 350, 2500)
    assert len(parts) > 1
    assert sorted(i for p in parts for i in p["segments"]) == list(range(40))  # nothing lost or repeated
    for p in parts:
        mb = (p["video_kbps"] + 96) * 1000 / 8 * p["duration"] / 1_000_000
        assert mb <= 15.0 and p["video_kbps"] >= 350


def test_split_segments_balanced_and_greedy_fallback():
    groups = split_segments([10, 10, 10, 10], max_len=25)
    assert groups == [[0, 1], [2, 3]]
    assert split_segments([30, 5], max_len=20) == [[0], [1]]  # a single over-long segment still gets its own part
    assert video_kbps_for(100, 15, 96) > 1000 and video_kbps_for(1000, 15, 96) < 350


# ------------------------------------------------------------------ context geometry
def test_video_size():
    cfg = deep_merge(DEFAULTS, {})
    ctx = Ctx(cfg=cfg, root=__import__("pathlib").Path("."), date="2026-11-13")
    assert ctx.video_size == (1280, 720)
    cfg["video"]["aspect"] = "portrait"
    assert ctx.video_size == (720, 1280)
    assert ctx.work_size == (1080, 1920)
