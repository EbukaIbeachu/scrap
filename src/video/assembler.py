"""Stage 6: build the highlights video with FFmpeg (images + voiceover + captions)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..context import Ctx
from ..images import make_card
from ..images.fallback_cards import render_title_card
from ..images.people import usable_people
from ..script import load_script
from ..utils import UserError, digest, file_digest, log, media_duration, run_ffmpeg
from ..voice import load_audio_meta
from .captions import (chunk_timings, render_caption_png, render_person_card, render_static_overlay,
                       split_caption_chunks, layout)
from ..utils import load_font

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".flac")


def work_dir(ctx: Ctx) -> Path:
    return ctx.subdir("work", ctx.date)


# --------------------------------------------------------------------------- segment inputs
def _title_image(ctx: Ctx, kind: str, seg: dict) -> Path:
    brand = ctx.get("video.brand", {})
    title = seg.get("headline") or brand.get("name", "News")
    subtitle = ctx.date if kind == "intro" else brand.get("name", "")
    dest = work_dir(ctx) / f"title_{kind}_{digest(title, n=8)}.jpg"
    return render_title_card(dest, ctx.work_size, title, subtitle, brand)


def segment_inputs(ctx: Ctx, index: int, seg: dict) -> tuple[list[Path], list[str]]:
    """(image paths in display order, credit lines) for one script segment."""
    if seg["kind"] in ("intro", "outro", "section"):
        return [_title_image(ctx, seg["kind"], seg)], []
    story = ctx.db.get_story(seg["story_id"])
    images: list[Path] = []
    credits: list[str] = []
    for row in ctx.db.images_for(seg["story_id"]):
        if Path(row["path"]).exists():
            images.append(Path(row["path"]))
            if row.get("credit"):
                credits.append(row["credit"])
    if not images and story:
        card = make_card(ctx, story, ctx.subdir("images", ctx.date, str(story["id"])))
        images.append(Path(card["path"]))
    for p in usable_people(ctx, seg["story_id"])[:2]:
        card_path = work_dir(ctx) / f"person_{p['id']}.jpg"
        render_person_card(Path(p["path"]), p["name"], p.get("role") or "", ctx.work_size,
                           ctx.get("video.brand", {}), card_path)
        images.append(card_path)
        if p.get("credit"):
            credits.append(p["credit"])
    return images, list(dict.fromkeys(credits))


def _spec_key(ctx: Ctx, seg: dict, images: list[Path], credits: list[str], audio: dict) -> str:
    stamp = [file_digest(p) for p in images]  # content, not mtime: cards are redrawn on every run
    return digest(json.dumps(seg, sort_keys=True, default=str), stamp, credits, audio.get("hash", audio.get("duration")),
                  ctx.video_size, ctx.get("video", {}), ctx.get("tts.speed"))


# --------------------------------------------------------------------------- one segment
def ken_burns(index: int, frames: int, dz: float) -> tuple[str, str, str]:
    """(z, x, y) zoompan expressions; alternates zoom-in, zoom-out and a slow pan."""
    variant = index % 3
    if variant == 1:
        return f"1+{dz}-{dz}*on/{frames}", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    if variant == 2:
        return f"1+{dz}*on/{frames}", f"(iw-iw/zoom)*on/{frames}", "(ih-ih/zoom)/2"
    return f"1+{dz}*on/{frames}", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"


def build_segment(ctx: Ctx, index: int, seg: dict, images: list[Path], credits: list[str],
                  audio: dict, out_path: Path) -> float:
    """Render one segment (visuals + overlays + voiceover) to ``out_path``. Returns its duration."""
    W, H = ctx.video_size
    fps = int(ctx.get("video.fps", 25))
    brand = ctx.get("video.brand", {})
    speech = float(audio["duration"])
    pad = float(ctx.get("video.segment_pad_seconds", 0.5))
    total = speech + pad
    n = len(images)
    tr = min(float(ctx.get("video.image_transition_seconds", 0.6)), total / (2 * n)) if n > 1 else 0.0
    per = (total + (n - 1) * tr) / n
    frames = max(int(math.ceil(per * fps)), 2)
    dz = float(ctx.get("video.zoom_amount", 0.12))

    # overlays
    wdir = ctx.subdir("work", ctx.date, f"seg_{index:03d}_overlays")
    overlays: list[tuple[Path, tuple[float, float] | None]] = []
    if seg["kind"] == "story":
        overlays.append((render_static_overlay(seg["headline"], seg["source"] or "", "; ".join(credits),
                                               (W, H), brand, wdir / "static.png"), None))
    lo = layout((W, H))
    font = load_font(lo["sub_font"], bold=True, custom=brand.get("font_path", ""))
    chunks = split_caption_chunks(seg["narration"], font, int(W - 2 * lo["margin"] * 1.6))
    for i, (chunk, span) in enumerate(zip(chunks, chunk_timings(chunks, speech))):
        overlays.append((render_caption_png(chunk, (W, H), brand, wdir / f"cap_{i:02d}.png"), span))

    # ffmpeg command
    cmd: list[object] = []
    for img in images:
        cmd += ["-i", img]
    cmd += ["-i", audio["path"]]
    a_idx = n
    for png, _ in overlays:
        cmd += ["-i", png]

    parts = []
    for i in range(n):
        # portraits of people get a gentler zoom so the photo frame is never cropped
        z, x, y = ken_burns(index + i, frames, dz * (0.35 if images[i].name.startswith("person_") else 1.0))
        parts.append(f"[{i}:v]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={fps},"
                     f"format=yuv420p,setsar=1[c{i}]")
    cur = "c0"
    for k in range(1, n):
        parts.append(f"[{cur}][c{k}]xfade=transition=fade:duration={tr:.3f}:offset={k * (per - tr):.3f}[x{k}]")
        cur = f"x{k}"
    for j, (png, span) in enumerate(overlays):
        enable = f":enable='between(t,{span[0]:.3f},{span[1]:.3f})'" if span else ""
        parts.append(f"[{cur}][{a_idx + 1 + j}:v]overlay=0:0:format=auto{enable}[o{j}]")
        cur = f"o{j}"
    parts.append(f"[{cur}]format=yuv420p[vout]")
    parts.append(f"[{a_idx}:a]aresample=44100,apad=whole_dur={total:.3f}[aout]")

    run_ffmpeg([
        *cmd, "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", ctx.get("video.preset", "veryfast"), "-crf", ctx.get("video.crf", 20),
        "-pix_fmt", "yuv420p", "-r", fps, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-t", f"{total:.3f}",
        out_path,
    ])
    return media_duration(out_path)


# --------------------------------------------------------------------------- whole video
def pick_music(ctx: Ctx) -> Path | None:
    folder = ctx.resolve_path(ctx.get("video.music_dir", "music"))
    if not folder.exists():
        return None
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not files:
        return None
    return files[int(digest(ctx.date, n=6), 16) % len(files)]


def _concat_list(paths: list[Path], dest: Path) -> Path:
    lines = ["file '" + str(p.resolve()).replace("\\", "/").replace("'", "'\\''") + "'" for p in paths]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def run_assemble(ctx: Ctx) -> Path:
    """Build every segment (cached), join them, add optional music. Returns the master video path."""
    segments = load_script(ctx)
    wdir = work_dir(ctx)
    master, meta_path = wdir / "master.mp4", wdir / "master.json"
    music = pick_music(ctx)

    seg_files: list[Path] = []
    keys: list[str] = []
    durations: list[float] = []
    for i, seg in enumerate(segments):
        audio = load_audio_meta(ctx, i)
        images, credits = segment_inputs(ctx, i, seg)
        key = _spec_key(ctx, seg, images, credits, audio)
        out = wdir / f"seg_{i:03d}.mp4"
        key_file = wdir / f"seg_{i:03d}.key"
        if out.exists() and key_file.exists() and key_file.read_text() == key and not ctx.force:
            log.info("Segment %d/%d unchanged, reusing", i + 1, len(segments))
            dur = media_duration(out)
        else:
            log.info("Rendering segment %d/%d (%s, %d image%s)", i + 1, len(segments), seg["kind"], len(images),
                     "" if len(images) == 1 else "s")
            dur = build_segment(ctx, i, seg, images, credits, audio, out)
            key_file.write_text(key)
        seg_files.append(out)
        keys.append(key)
        durations.append(dur)

    master_key = digest(*keys, music, ctx.get("video.music_volume"))
    if master.exists() and meta_path.exists() and not ctx.force:
        if json.loads(meta_path.read_text(encoding="utf-8")).get("key") == master_key:
            log.info("Master video unchanged: %s", master)
            return master

    listing = _concat_list(seg_files, wdir / "segments.txt")
    if music:
        vol = float(ctx.get("video.music_volume", 0.07))
        log.info("Adding background music: %s", music.name)
        run_ffmpeg([
            "-f", "concat", "-safe", 0, "-i", listing, "-stream_loop", -1, "-i", music,
            "-filter_complex",
            f"[1:a]volume={vol}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2,alimiter=limit=0.95[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", master,
        ])
    else:
        run_ffmpeg(["-f", "concat", "-safe", 0, "-i", listing, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", master])

    meta = {
        "key": master_key,
        "duration": media_duration(master),
        "segments": [{"kind": s["kind"], "headline": s["headline"], "duration": d} for s, d in zip(segments, durations)],
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("Master video ready: %.1fs", meta["duration"])
    return master


def load_master(ctx: Ctx) -> tuple[Path, dict]:
    wdir = work_dir(ctx)
    master, meta_path = wdir / "master.mp4", wdir / "master.json"
    if not master.exists() or not meta_path.exists():
        raise UserError("No assembled video yet. Run:  python main.py assemble")
    return master, json.loads(meta_path.read_text(encoding="utf-8"))
