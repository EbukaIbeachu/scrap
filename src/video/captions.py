"""Overlay graphics drawn with Pillow: subtitles, lower-third, source pill, person cards.

Everything is rendered to transparent PNGs that FFmpeg overlays, so no FFmpeg text/font
features (drawtext, libass) are required and it works the same on Windows, macOS and Linux.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..images.fallback_cards import _brand_colors, wrap_text
from ..utils import load_font


# --------------------------------------------------------------------------- layout
def layout(size: tuple[int, int]) -> dict:
    """Proportional layout used by every overlay (so landscape and portrait both look right)."""
    w, h = size
    short = min(w, h)
    bar_h = int(max(h * 0.13, short * 0.19))
    sub_font = int(short * 0.052)
    sub_block = int(sub_font * 2 * 1.3 + sub_font * 0.9)
    sub_bottom = h - bar_h - int(h * 0.02)
    margin = int(w * 0.04)
    pill_font = max(int(short * 0.036), 12)
    pill_h = pill_font + 2 * int(pill_font * 0.55) - 4
    return {
        "w": w, "h": h, "short": short, "margin": margin, "bar_h": bar_h,
        "sub_font": sub_font, "sub_block": sub_block,
        "sub_bottom": sub_bottom, "sub_top": sub_bottom - sub_block,
        "pill_font": pill_font, "pill_h": pill_h, "pill_bottom": margin + pill_h,
    }


# --------------------------------------------------------------------------- subtitle chunks (pure)
def split_caption_chunks(text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    """Split narration into subtitle chunks of at most ``max_lines`` lines that fit ``max_width``."""
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    chunks: list[str] = []
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if not cur or measure.textlength(trial, font=font) <= max_width:
            cur = trial
            continue
        lines.append(cur)
        cur = word
        if len(lines) == max_lines:
            chunks.append(" ".join(lines))
            lines = []
    if cur:
        lines.append(cur)
    if lines:
        chunks.append(" ".join(lines))
    return chunks


def chunk_timings(chunks: list[str], total: float, lead: float = 0.1) -> list[tuple[float, float]]:
    """Show each chunk for a time proportional to its length; the last one ends at ``total``."""
    if not chunks:
        return []
    weights = [len(c) + 8 for c in chunks]
    span = max(total - lead, 0.5)
    out, t = [], lead
    for wgt in weights:
        dur = span * wgt / sum(weights)
        out.append((round(t, 3), round(t + dur, 3)))
        t += dur
    return out


# --------------------------------------------------------------------------- drawing
def _blank(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def render_caption_png(text: str, size: tuple[int, int], brand: dict, path: Path) -> Path:
    """One subtitle chunk: white bold text on a translucent rounded box, above the lower third."""
    lo = layout(size)
    font = load_font(lo["sub_font"], bold=True, custom=brand.get("font_path", ""))
    img = _blank(size)
    draw = ImageDraw.Draw(img)
    lines = wrap_text(draw, text, font, int(size[0] - 2 * lo["margin"] * 1.6))
    line_h = int(font.size * 1.3)
    text_w = max(draw.textlength(line, font=font) for line in lines)
    pad = int(font.size * 0.45)
    box_w, box_h = int(text_w + 2 * pad), line_h * len(lines) + 2 * pad - int(font.size * 0.3)
    x0 = (size[0] - box_w) // 2
    y1 = lo["sub_bottom"]
    y0 = y1 - box_h
    draw.rounded_rectangle([x0, y0, x0 + box_w, y1], radius=int(font.size * 0.4), fill=(0, 0, 0, 165))
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        draw.text(((size[0] - lw) / 2, y0 + pad - int(font.size * 0.1) + i * line_h), line, font=font, fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def render_static_overlay(
    headline: str, source: str, credit: str, size: tuple[int, int], brand: dict, path: Path
) -> Path:
    """Headline lower-third + 'Source: X' pill (+ photo credit). Shown for the whole story."""
    lo = layout(size)
    w, h, margin = lo["w"], lo["h"], lo["margin"]
    primary, accent, text = _brand_colors(brand)
    font_path = brand.get("font_path", "")
    img = _blank(size)
    draw = ImageDraw.Draw(img)

    # lower third: headline on top, photo/image credit in small type at the bottom of the bar
    top = h - lo["bar_h"]
    draw.rectangle([0, top, w, h], fill=(*primary, 238))
    draw.rectangle([0, top, max(6, int(w * 0.012)), h], fill=(*accent, 255))
    text_x = margin + int(w * 0.012)
    max_w = w - text_x - margin
    pad = int(lo["bar_h"] * 0.08)

    tiny = load_font(max(int(lo["short"] * 0.026), 10), custom=font_path)
    credit_lines = wrap_text(draw, credit, tiny, max_w)[:2] if credit else []
    credit_line_h = int(tiny.size * 1.25)
    credit_h = len(credit_lines) * credit_line_h
    avail = lo["bar_h"] - credit_h - 2 * pad

    size_px = int(lo["bar_h"] * 0.26)
    while True:
        font = load_font(size_px, bold=True, custom=font_path)
        lines = wrap_text(draw, headline, font, max_w)
        line_h = int(font.size * 1.2)
        if (len(lines) <= 2 and len(lines) * line_h <= avail) or size_px <= 12:
            break
        size_px = int(size_px * 0.92)
    lines = lines[:2]
    y = top + pad + max(0, (avail - len(lines) * line_h) // 2)
    for i, line in enumerate(lines):
        draw.text((text_x, y + i * line_h), line, font=font, fill=(*text, 255))
    cy = h - pad - credit_h
    for line in credit_lines:
        draw.text((text_x, cy), line, font=tiny, fill=(*text, 215))
        cy += credit_line_h

    # source pill (top right)
    small = load_font(lo["pill_font"], bold=True, custom=font_path)
    label = f"Source: {source}"
    lw = draw.textlength(label, font=small)
    ppad = int(small.size * 0.55)
    x1, y0 = w - margin, margin
    draw.rounded_rectangle([x1 - lw - 2 * ppad, y0, x1, y0 + lo["pill_h"]], radius=int(small.size * 0.5),
                           fill=(0, 0, 0, 170))
    draw.text((x1 - lw - ppad, y0 + ppad - 3), label, font=small, fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def render_person_card(
    photo: Path, name: str, role: str, size: tuple[int, int], brand: dict, path: Path
) -> Path:
    """Full-frame card: blurred backdrop, framed portrait, name and role plate."""
    lo = layout(size)
    w, h = size
    primary, accent, text = _brand_colors(brand)
    font_path = brand.get("font_path", "")

    src = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
    bg = ImageOps.fit(src, size, Image.Resampling.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(12, w // 60)))
    bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), 0.45)

    plate_h = int(lo["short"] * 0.13)
    gap = int(h * 0.015)
    # in portrait the photo is wide relative to the frame, so keep it clear of the "Source" pill
    area_top = lo["pill_bottom"] + 8 if h > w else int(h * 0.06)
    area_bottom = lo["sub_top"] - int(h * 0.02) - plate_h - gap
    box_w, box_h = int(w * 0.7), max(area_bottom - area_top, 50)
    fg = src.copy()
    fg.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
    border = max(4, w // 250)
    framed = ImageOps.expand(fg, border=border, fill=(255, 255, 255))
    fx = (w - framed.width) // 2
    fy = area_top + (box_h - framed.height) // 2
    bg.paste(framed, (fx, fy))

    draw = ImageDraw.Draw(bg)
    name_font = load_font(int(plate_h * 0.42), bold=True, custom=font_path)
    role_font = load_font(int(plate_h * 0.30), custom=font_path)
    plate_w = int(max(draw.textlength(name, font=name_font), draw.textlength(role, font=role_font)) + plate_h * 0.8)
    plate_w = min(max(plate_w, framed.width), w - 2 * lo["margin"])
    px, py = (w - plate_w) // 2, fy + framed.height + gap
    draw.rectangle([px, py, px + plate_w, py + plate_h], fill=primary)
    draw.rectangle([px, py, px + max(6, w // 160), py + plate_h], fill=accent)
    draw.text((px + plate_h * 0.3, py + plate_h * 0.10), name, font=name_font, fill=text)
    if role:
        draw.text((px + plate_h * 0.3, py + plate_h * 0.58), role, font=role_font, fill=tuple(int(c * 0.9) for c in text))
    path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(path, "JPEG", quality=90)
    return path
