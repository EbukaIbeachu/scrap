"""Stage 7: WhatsApp-friendly export (H.264 + AAC, MP4, under ~16 MB, split into parts if needed)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..context import Ctx
from ..review import write_review
from ..utils import digest, file_mb, log, media_duration, run_ffmpeg
from .assembler import load_master
from .subtitles import write_srt

COPYRIGHT_WARNING = (
    "NOTE: News photos and text are usually protected by copyright. Only share this video "
    "with people/places where you have the right to share it. Check the review file for "
    "image sources and licences before sending it anywhere."
)


# --------------------------------------------------------------------------- planning (pure)
def video_kbps_for(duration_s: float, target_mb: float, audio_kbps: int, safety: float = 0.95) -> float:
    """Video bitrate (kbit/s) that keeps a file of ``duration_s`` under ``target_mb`` decimal MB."""
    bits = target_mb * 1_000_000 * 8 * safety
    return bits / max(duration_s, 0.1) / 1000 - audio_kbps


def split_segments(durations: list[float], max_len: float) -> list[list[int]]:
    """Group consecutive segments into parts no longer than ``max_len`` seconds (balanced if possible)."""
    total = sum(durations)
    n = max(1, math.ceil(total / max_len))
    target = total / n
    groups: list[list[int]] = [[] for _ in range(n)]
    pos = 0.0
    for i, d in enumerate(durations):
        groups[min(int((pos + d / 2) / target), n - 1)].append(i)
        pos += d
    groups = [g for g in groups if g]
    if all(sum(durations[i] for i in g) <= max_len + 1e-6 for g in groups):
        return groups
    # fallback: plain greedy packing
    groups, cur, acc = [], [], 0.0
    for i, d in enumerate(durations):
        if cur and acc + d > max_len:
            groups.append(cur)
            cur, acc = [], 0.0
        cur.append(i)
        acc += d
    if cur:
        groups.append(cur)
    return groups


def plan_export(durations: list[float], target_mb: float, audio_kbps: int, min_kbps: int, max_kbps: int
                ) -> list[dict]:
    """Return parts: [{'segments': [...], 'duration': s, 'video_kbps': k}]. One part if it fits."""
    total = sum(durations)
    one = video_kbps_for(total, target_mb, audio_kbps)
    if one >= min_kbps:
        return [{"segments": list(range(len(durations))), "duration": total, "video_kbps": int(min(one, max_kbps))}]
    max_len = target_mb * 1_000_000 * 8 * 0.95 / ((min_kbps + audio_kbps) * 1000)
    parts = []
    for g in split_segments(durations, max_len):
        dur = sum(durations[i] for i in g)
        k = video_kbps_for(dur, target_mb, audio_kbps)
        parts.append({"segments": g, "duration": dur, "video_kbps": int(min(max(k, min_kbps), max_kbps))})
    return parts


# --------------------------------------------------------------------------- encoding
def _encode(ctx: Ctx, master: Path, out: Path, start: float, dur: float, vkbps: int, akbps: int) -> None:
    run_ffmpeg([
        "-ss", f"{start:.3f}", "-i", master, "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", ctx.get("export.preset", "medium"), "-profile:v", "main",
        "-b:v", f"{vkbps}k", "-maxrate", f"{int(vkbps * 1.25)}k", "-bufsize", f"{vkbps * 2}k",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{akbps}k", "-ac", 2, "-movflags", "+faststart", out,
    ])


def encode_part(ctx: Ctx, master: Path, out: Path, start: float, dur: float, vkbps: int) -> float:
    """Encode one part, lowering the bitrate and retrying (up to 3 times) if it is still too big."""
    target = float(ctx.get("export.target_mb", 15.0))
    akbps = int(ctx.get("export.audio_kbps", 96))
    floor = 120
    for attempt in range(4):
        _encode(ctx, master, out, start, dur, vkbps, akbps)
        size = file_mb(out)
        if size <= target or vkbps <= floor:
            break
        vkbps = max(int(vkbps * target / size * 0.93), floor)
        log.info("  %.1f MB is over the %.1f MB target; retrying at %d kbps", size, target, vkbps)
    return file_mb(out)


def run_export(ctx: Ctx) -> dict:
    """Export WhatsApp-ready file(s) into data/output and write a manifest. Returns the manifest."""
    master, meta = load_master(ctx)
    out_dir = ctx.subdir("output")
    manifest_path = out_dir / f"export_{ctx.date}.json"
    review = write_review(ctx)
    cfg = ctx.get("export", {})
    key = digest(meta["key"], json.dumps(cfg, sort_keys=True))

    if manifest_path.exists() and not ctx.force:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("key") == key and all(Path(f["path"]).exists() for f in manifest["files"]):
            log.info("Export unchanged: %s", manifest["files"][0]["path"])
            print(COPYRIGHT_WARNING)
            return manifest

    durations = [s["duration"] for s in meta["segments"]]
    parts = plan_export(durations, float(cfg["target_mb"]), int(cfg["audio_kbps"]),
                        int(cfg["min_video_kbps"]), int(cfg["max_video_kbps"]))
    n = len(parts)
    files = []
    for i, part in enumerate(parts, start=1):
        name = f"news_highlights_{ctx.date}.mp4" if n == 1 else f"news_highlights_{ctx.date}_part{i}of{n}.mp4"
        out = out_dir / name
        start = sum(durations[:part["segments"][0]])
        log.info("Exporting %s (%.0fs at %d kbps)", name, part["duration"], part["video_kbps"])
        size = encode_part(ctx, master, out, start, part["duration"], part["video_kbps"])
        files.append({"path": str(out), "duration": media_duration(out), "size_mb": round(size, 2)})

    srt_name = f"news_highlights_{ctx.date}.srt"
    srt_path = write_srt(ctx, out_dir / srt_name)

    manifest = {
        "date": ctx.date, "key": key, "files": files, "n_parts": n,
        "n_stories": sum(1 for s in meta["segments"] if s["kind"] == "story"),
        "review": str(review), "srt": str(srt_path), "total_duration": meta["duration"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(COPYRIGHT_WARNING)
    return manifest
