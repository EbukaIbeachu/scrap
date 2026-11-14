"""Branded headline / title cards drawn with Pillow (no third-party content)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ..utils import hex_to_rgb, load_font


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap using real glyph widths."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    strip = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        strip.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return strip.resize((w, h))


def _brand_colors(brand: dict) -> tuple[tuple, tuple, tuple]:
    primary = hex_to_rgb(brand.get("primary_color", "#0B3D91"), (11, 61, 145))
    accent = hex_to_rgb(brand.get("accent_color", "#F2A900"), (242, 169, 0))
    text = hex_to_rgb(brand.get("text_color", "#FFFFFF"), (255, 255, 255))
    return primary, accent, text


def render_card(
    path: Path,
    size: tuple[int, int],
    headline: str,
    kicker: str = "",
    footer: str = "",
    brand: dict | None = None,
) -> Path:
    """Draw a branded card: gradient background, accent bar, kicker, big headline, footer."""
    brand = brand or {}
    primary, accent, text = _brand_colors(brand)
    w, h = size
    dark = tuple(int(c * 0.45) for c in primary)
    img = _gradient(size, primary, dark)
    draw = ImageDraw.Draw(img)
    font_path = brand.get("font_path", "")

    margin = int(w * 0.08)
    short = min(w, h)
    head_font = load_font(max(int(short * 0.085), 18), bold=True, custom=font_path)
    small_font = load_font(max(int(short * 0.04), 12), bold=True, custom=font_path)
    foot_font = load_font(max(int(short * 0.035), 11), custom=font_path)

    lines = wrap_text(draw, headline, head_font, w - 2 * margin)
    while len(lines) > 5 and head_font.size > 16:  # shrink for very long headlines
        head_font = load_font(int(head_font.size * 0.88), bold=True, custom=font_path)
        lines = wrap_text(draw, headline, head_font, w - 2 * margin)
    line_h = int(head_font.size * 1.25)
    block_h = line_h * len(lines)
    y = (h - block_h) // 2

    draw.rectangle([margin, y - int(short * 0.07), margin + int(w * 0.12), y - int(short * 0.07) + max(6, h // 120)],
                   fill=accent)
    if kicker:
        draw.text((margin, y - int(short * 0.07) - int(small_font.size * 1.6)), kicker.upper(), font=small_font, fill=accent)
    for i, line in enumerate(lines):
        draw.text((margin, y + i * line_h), line, font=head_font, fill=text)
    if footer:
        draw.text((margin, h - margin - foot_font.size), footer, font=foot_font, fill=tuple(int(c * 0.85) for c in text))

    logo = brand.get("logo_path", "")
    if logo and Path(logo).exists():
        try:
            lg = Image.open(logo).convert("RGBA")
            lg.thumbnail((int(w * 0.16), int(h * 0.12)))
            img.paste(lg, (w - margin - lg.width, margin), lg)
        except Exception:
            pass

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=90)
    return path


def render_headline_card(path: Path, size: tuple[int, int], headline: str, outlet: str, date: str, brand: dict) -> Path:
    return render_card(path, size, headline, kicker=brand.get("name", "News"), footer=f"{outlet}  |  {date}", brand=brand)


def render_title_card(path: Path, size: tuple[int, int], title: str, subtitle: str, brand: dict) -> Path:
    return render_card(path, size, title, kicker=brand.get("name", "News"), footer=subtitle, brand=brand)
