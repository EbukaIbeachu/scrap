"""Shared helpers: logging, subprocess, FFmpeg, fonts, small file utilities."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger("news_highlights")


class UserError(Exception):
    """A problem the user can fix. Shown as a plain message without a traceback."""


# --------------------------------------------------------------------------- logging
def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Log to the console and to ``logs/run.log``."""
    log_dir.mkdir(parents=True, exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        try:  # avoid UnicodeEncodeError on Windows consoles
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    logger = logging.getLogger("news_highlights")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)


# --------------------------------------------------------------------------- strings
def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len] or "item"


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def digest(*parts: Any, n: int = 12) -> str:
    """Short stable hash of the given values (used for cache keys)."""
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:n]


def file_digest(path: Path, n: int = 12) -> str:
    """Hash of a file's CONTENT (stable even if the file is rewritten with identical bytes)."""
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:n]


def now_local(tz_name: str | None = None) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
    except Exception:
        pass
    return datetime.now().astimezone()


# --------------------------------------------------------------------------- subprocess / ffmpeg
def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise UserError(
            "FFmpeg was not found on this computer.\n"
            "  Windows: open PowerShell and run  winget install Gyan.FFmpeg  (then reopen the terminal)\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise UserError("ffprobe was not found. It is installed together with FFmpeg.")
    return path


def run_cmd(cmd: Sequence[object], timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a command without a shell; raise RuntimeError with stderr on failure."""
    argv = [str(c) for c in cmd]
    log.debug("run: %s", " ".join(argv)[:600])
    res = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    if res.returncode != 0:
        raise RuntimeError(f"{Path(argv[0]).name} failed ({res.returncode}):\n{res.stderr[-1800:]}")
    return res


def run_ffmpeg(args: Sequence[object], timeout: float | None = None) -> None:
    run_cmd([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args], timeout=timeout)


def media_duration(path: Path) -> float:
    res = run_cmd(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path]
    )
    return float(res.stdout.strip())


def file_mb(path: Path) -> float:
    """Size in decimal megabytes (1 MB = 1,000,000 bytes)."""
    return path.stat().st_size / 1_000_000


# --------------------------------------------------------------------------- interaction / OS
def is_interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout.isatty())


def ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def downloads_dir(configured: str = "") -> Path:
    path = Path(configured).expanduser() if configured else Path.home() / "Downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_path(path: Path) -> None:
    """Open a file or folder with the system default app (best effort)."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # pragma: no cover
        log.warning("Could not open %s: %s", path, exc)


# --------------------------------------------------------------------------- fonts
_BOLD = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_REGULAR = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def load_font(size: int, bold: bool = False, custom: str = ""):
    """Load a TrueType font (custom path first, then common system fonts)."""
    from PIL import ImageFont

    candidates = ([custom] if custom else []) + (_BOLD if bold else _REGULAR)
    for cand in candidates:
        if cand and Path(cand).exists():
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def hex_to_rgb(value: str, default: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    value = (value or "").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return default
