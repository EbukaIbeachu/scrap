"""Stage 8: ask before copying the finished video to your Downloads folder.

This only COPIES files on your own computer. It never uploads, sends or posts anything.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .context import Ctx
from .utils import ask_yes_no, downloads_dir, is_interactive, log, now_local, open_path


def _manifests(ctx: Ctx) -> list[Path]:
    return sorted(ctx.subdir("output").glob("export_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def print_summary(manifest: dict) -> None:
    files = manifest["files"]
    print("\n" + "=" * 60)
    print("YOUR VIDEO IS READY")
    print("=" * 60)
    for f in files:
        print(f"  {Path(f['path']).name}   {f['duration']:.0f}s   {f['size_mb']:.1f} MB")
    print(f"  Stories: {manifest['n_stories']}   Parts: {manifest['n_parts']}")
    if manifest.get("srt"):
        print(f"  Subtitles: {manifest['srt']}")
    print(f"  Review file: {manifest['review']}")
    print("=" * 60)


def copy_to_downloads(ctx: Ctx, manifest: dict, stamp: str | None = None) -> list[Path]:
    """Copy the video file(s) and SRT to the download folder. Never overwrites."""
    dest_dir = downloads_dir(ctx.get("download.download_dir", ""))
    stamp = stamp or now_local(ctx.get("general.timezone")).strftime("%Y-%m-%d_%H%M")
    copied: list[Path] = []
    for i, f in enumerate(manifest["files"], start=1):
        suffix = "" if manifest["n_parts"] == 1 else f"_part{i}of{manifest['n_parts']}"
        target = dest_dir / f"news_highlights_{stamp}{suffix}.mp4"
        n = 2
        while target.exists():  # never overwrite an earlier download
            target = dest_dir / f"news_highlights_{stamp}{suffix}_{n}.mp4"
            n += 1
        shutil.copy2(f["path"], target)
        copied.append(target)
    srt_src = manifest.get("srt")
    if srt_src and Path(srt_src).exists():
        srt_target = dest_dir / f"news_highlights_{stamp}.srt"
        n = 2
        while srt_target.exists():
            srt_target = dest_dir / f"news_highlights_{stamp}_{n}.srt"
            n += 1
        shutil.copy2(srt_src, srt_target)
        copied.append(srt_target)
    return copied


def offer_download(ctx: Ctx, manifest: dict, yes: bool = False, prompt: bool = True) -> list[Path]:
    """Print the summary and ask 'Download it now?'. Returns the copied files (empty if declined)."""
    print_summary(manifest)
    if not prompt:
        log.info("Download prompt skipped. Video stays in: %s", Path(manifest["files"][0]["path"]).parent)
        return []
    if not yes and not is_interactive():
        log.info("Not an interactive terminal, so no question asked. Video stays in: %s",
                 Path(manifest["files"][0]["path"]).parent)
        return []
    if yes or ask_yes_no("Your video is ready. Download it now?", default=False):
        copied = copy_to_downloads(ctx, manifest)
        for c in copied:
            print(f"Saved: {c}")
        if not yes and is_interactive() and ask_yes_no("Open the folder?", default=False):
            open_path(copied[0].parent)
        return copied
    print("Not downloaded. The video stays here:")
    for f in manifest["files"]:
        print(f"  {f['path']}")
    print("Download it later with:  python main.py download")
    return []


def download_command(ctx: Ctx, yes: bool = False) -> list[Path]:
    """List past videos (newest first) and let the user pick which to copy to Downloads."""
    items = []
    for m in _manifests(ctx):
        data = json.loads(m.read_text(encoding="utf-8"))
        if all(Path(f["path"]).exists() for f in data["files"]):
            items.append(data)
    if not items:
        print("No exported videos found. Run:  python main.py run-all")
        return []
    print("\nPast videos (newest first):")
    for i, d in enumerate(items, start=1):
        mtime = datetime.fromtimestamp(Path(d["files"][0]["path"]).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {i}. {d['date']}  {d['total_duration']:.0f}s  {d['n_stories']} stories  "
              f"{d['n_parts']} part(s)  (made {mtime})")
    if yes or not is_interactive():
        choice = "1"
    else:
        choice = input("Number(s) to download, comma separated (Enter to cancel): ").strip()
    copied: list[Path] = []
    for tok in [t.strip() for t in choice.split(",") if t.strip()]:
        if tok.isdigit() and 1 <= int(tok) <= len(items):
            copied += copy_to_downloads(ctx, items[int(tok) - 1])
    for c in copied:
        print(f"Saved: {c}")
    return copied
