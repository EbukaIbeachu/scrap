"""Full pipeline, offline: local RSS file + local images, mock voice, real FFmpeg.

Wikipedia is replaced by a fake so no network is needed. Claude is not called (--dry-run).
"""
import io
import json
import shutil
import subprocess
from email.utils import formatdate
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageDraw

from src.cli import main
from src.images import people as people_mod

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg not installed")

DATE = "2026-11-13"


def _jpeg(path: Path, size, color, circle=False) -> bytes:
    img = Image.new("RGB", size, color)
    if circle:
        d = ImageDraw.Draw(img)
        w, h = size
        d.ellipse([w * 0.25, h * 0.15, w * 0.75, h * 0.55], fill=(240, 200, 170))
        d.rectangle([w * 0.2, h * 0.58, w * 0.8, h], fill=(30, 30, 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def _body(topic: str) -> str:
    return " ".join(
        f"{topic} officials said on Friday that the programme will continue across several states this year, "
        f"and residents welcomed the announcement during public meetings held in different towns." for _ in range(6))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    for i, color in enumerate([(30, 90, 160), (160, 70, 40), (40, 130, 70)], start=1):
        _jpeg(assets / f"img{i}.jpg", (1200, 800), color)
    portrait = _jpeg(assets / "portrait.jpg", (600, 800), (90, 90, 120), circle=True)

    now = formatdate(usegmt=True)
    items = []
    for i, (title, extra) in enumerate([
        ("Road project opens in Lagos", "President Bola Tinubu said the road is ready. "),
        ("Farmers get new support", ""),
        ("School feeding expands", ""),
    ], start=1):
        items.append(
            f"<item><title>{title}</title><link>https://ex.test/{i}</link><pubDate>{now}</pubDate>"
            f"<description>{title} summary.</description>"
            f'<media:content url="file://{assets}/img{i}.jpg" medium="image"/>'
            f"<content:encoded><![CDATA[<p>{extra}{_body(title)}</p>]]></content:encoded></item>")
    items.append(f"<item><title>Short brief only</title><link>https://ex.test/4</link><pubDate>{now}</pubDate>"
                 f"<description>Only a short summary exists for this story.</description></item>")
    feed = tmp_path / "feed.xml"
    feed.write_text(
        '<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>T</title>'
        + "".join(items) + "</channel></rss>", encoding="utf-8")

    downloads = tmp_path / "downloads"
    cfg = {
        "general": {"allow_file_urls": True, "request_delay_seconds": 0, "data_dir": str(tmp_path / "data")},
        "outlets": [{"name": "Test Wire", "rss_url": f"file://{feed}"}],
        "collector": {"fetch_full_text": False},
        "selection": {"top_n": 4, "max_per_outlet": 5},
        "video": {"height": 360, "fps": 10, "preset": "ultrafast", "crf": 28},
        "export": {"preset": "ultrafast"},
        "download": {"download_dir": str(downloads)},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    # fake Wikipedia/Commons
    monkeypatch.setattr(people_mod.WikiClient, "candidates", lambda self, name: [{
        "title": "Bola Tinubu", "extract": "Bola Tinubu is a Nigerian politician, the president of Nigeria.",
        "disambiguation": False, "pageimage": "Tinubu.jpg", "url": "https://en.wikipedia.org/wiki/Bola_Tinubu"}])
    monkeypatch.setattr(people_mod.WikiClient, "commons_file", lambda self, fn: {
        "url": "http://fake/x.jpg", "page": "https://commons.wikimedia.org/wiki/File:Tinubu.jpg",
        "license": "CC BY-SA 4.0", "artist": "Test Author", "mime": "image/jpeg"})
    monkeypatch.setattr(people_mod.WikiClient, "download", lambda self, url: portrait)
    return tmp_path, str(cfg_path), downloads


def run(cfg: str, *args: str) -> int:
    return main([*args, "--config", cfg, "--date", DATE, "--dry-run"])


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                          "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(out.stdout)


def test_full_pipeline(project):
    tmp, cfg, downloads = project
    data = tmp / "data"

    assert run(cfg, "collect") == 0
    assert run(cfg, "select") == 0
    assert run(cfg, "collect") == 0  # idempotent: no duplicates
    from src.db import Database
    assert Database(data / "news.db").count_stories() == 4

    assert run(cfg, "images") == 0
    imgs = sorted((data / "images" / DATE).glob("*/*.jpg"))
    assert len(imgs) >= 4
    assert all(Image.open(p).size == (960, 540) for p in imgs if p.name.startswith(("article", "card")))

    assert run(cfg, "people") == 0
    assert main(["approve-people", "--all", "--config", cfg, "--date", DATE]) == 0

    assert run(cfg, "script") == 0
    script = json.loads((data / "scripts" / f"{DATE}.json").read_text(encoding="utf-8"))
    assert [s["kind"] for s in script] == ["intro", "story", "story", "story", "story", "outro"]
    assert any(s["people"] and s["people"][0]["name"] == "Bola Tinubu" for s in script)

    assert run(cfg, "voice") == 0
    assert len(list((data / "audio" / DATE).glob("seg_*.wav"))) == 6

    assert run(cfg, "assemble") == 0
    master = data / "work" / DATE / "master.mp4"
    info = probe(master)
    kinds = {s["codec_type"] for s in info["streams"]}
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert kinds == {"video", "audio"} and (video["width"], video["height"]) == (640, 360)
    assert float(info["format"]["duration"]) > 20

    assert run(cfg, "export") == 0
    manifest = json.loads((data / "output" / f"export_{DATE}.json").read_text(encoding="utf-8"))
    assert manifest["n_parts"] == 1 and manifest["n_stories"] == 4
    out = Path(manifest["files"][0]["path"])
    assert out.exists() and out.stat().st_size < 15_000_000
    assert probe(out)["streams"][0]["codec_name"] in ("h264", "aac")

    review = (data / "output" / f"review_{DATE}.md").read_text(encoding="utf-8")
    assert "APPROVED" in review and "Bola Tinubu" in review and "CC BY-SA 4.0" in review
    assert "DRY RUN" in review and "only a short summary" in review.lower()

    # unchanged inputs -> everything is reused
    t0 = out.stat().st_mtime_ns
    assert run(cfg, "assemble") == 0 and run(cfg, "export") == 0
    assert out.stat().st_mtime_ns == t0

    # download copies, never overwrites
    assert main(["download", "--yes", "--config", cfg, "--date", DATE]) == 0
    assert main(["download", "--yes", "--config", cfg, "--date", DATE]) == 0
    assert len(list(downloads.glob("news_highlights_*.mp4"))) == 2
    # the original stays in staging
    assert out.exists()


def test_unapproved_person_photo_is_not_used(project):
    tmp, cfg, _ = project
    for cmd in ("collect", "images", "people"):
        assert run(cfg, cmd) == 0
    from src.db import Database
    row = Database(tmp / "data" / "news.db").conn.execute("SELECT approved, confidence FROM people_photos").fetchone()
    assert row["approved"] is None and row["confidence"] == 0.9
    assert run(cfg, "script") == 0
    script = json.loads((tmp / "data" / "scripts" / f"{DATE}.json").read_text(encoding="utf-8"))
    assert all(not s["people"] for s in script)  # require_approval defaults to true
