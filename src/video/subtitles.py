"""SRT subtitle file: one entry per narration chunk, timestamps matching the video."""
from __future__ import annotations

import re
from pathlib import Path

from ..context import Ctx
from ..script import load_script
from ..utils import UserError, log
from ..voice import load_audio_meta
from .captions import chunk_timings


# --------------------------------------------------------------------------- helpers (pure)
def _srt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT standard)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_chunks(text: str, max_chars: int = 84) -> list[str]:
    """Split narration into subtitle-sized chunks.

    Sentences stay together when they fit; a sentence longer than max_chars
    is split at word boundaries.  Max ~84 chars ≈ two 42-char subtitle lines.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    pending = ""
    for sent in sentences:
        if len(sent) > max_chars:
            if pending:
                chunks.append(pending)
                pending = ""
            line = ""
            for word in sent.split():
                trial = f"{line} {word}".strip()
                if line and len(trial) > max_chars:
                    chunks.append(line)
                    line = word
                else:
                    line = trial
            if line:
                chunks.append(line)
        elif not pending:
            pending = sent
        elif len(pending) + 1 + len(sent) <= max_chars:
            pending += " " + sent
        else:
            chunks.append(pending)
            pending = sent
    if pending:
        chunks.append(pending)
    return chunks or [text]


def build_srt_entries(
    segments: list[dict],
    audio_metas: list[dict],
    pad: float,
) -> list[tuple[float, float, str]]:
    """Return (abs_start, abs_end, text) for every subtitle chunk across all segments."""
    entries: list[tuple[float, float, str]] = []
    cursor = 0.0
    for seg, meta in zip(segments, audio_metas):
        speech = float(meta["duration"])
        text = (seg.get("narration") or "").strip()
        if text:
            chunks = _srt_chunks(text)
            for chunk, (t0, t1) in zip(chunks, chunk_timings(chunks, speech)):
                entries.append((cursor + t0, cursor + t1, chunk))
        cursor += speech + pad
    return entries


# --------------------------------------------------------------------------- public API
def write_srt(ctx: Ctx, out_path: Path) -> Path:
    """Write an SRT subtitle file for today's video.

    Timing mirrors the assembled video: segment durations come from the
    cached audio metadata, with the same pad applied between segments.
    """
    segments = load_script(ctx)
    if not segments:
        raise UserError("No script found. Run:  python main.py script")
    pad = float(ctx.get("video.segment_pad_seconds", 0.5))
    audio_metas = [load_audio_meta(ctx, i) for i in range(len(segments))]
    entries = build_srt_entries(segments, audio_metas, pad)

    lines: list[str] = []
    for idx, (t0, t1, text) in enumerate(entries, start=1):
        lines += [str(idx), f"{_srt_time(t0)} --> {_srt_time(t1)}", text, ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("SRT subtitles written: %s  (%d entries)", out_path, len(entries))
    return out_path
