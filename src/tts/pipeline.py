"""Voiceover pipeline (Stage 5 orchestrator).

Reads today's script from ``data/scripts/<date>.json``, synthesizes one audio
file per part (intro, one per story, outro) using the configured provider with
automatic fallback, records durations, and writes an audio manifest for the
assembly stage. Idempotent: existing audio files are reused unless ``force``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config
from .base import TTSProvider, audio_duration, normalize_for_speech, resolve_providers

logger = logging.getLogger(__name__)

_AUDIO_EXTS = ("mp3", "wav")


def _load_script(cfg: Config, date_str: str) -> tuple[dict | None, Path]:
    """Load today's script document, or ``(None, path)`` when missing."""
    path = cfg.data_dir / "scripts" / f"{date_str}.json"
    if not path.exists():
        return None, path
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read script %s: %s", path, exc)
        return None, path


def build_parts(doc: dict) -> list[dict[str, Any]]:
    """Turn a script document into the ordered list of audio parts."""
    parts: list[dict[str, Any]] = []
    if doc.get("intro"):
        parts.append({"kind": "intro", "key": "intro", "headline": "Intro", "text": doc["intro"]})
    for i, seg in enumerate(doc.get("segments", []), 1):
        parts.append(
            {
                "kind": "segment",
                "key": f"{i:03d}",
                "story_id": seg.get("story_id"),
                "headline": seg.get("headline", ""),
                "text": seg.get("narration", ""),
            }
        )
    if doc.get("outro"):
        parts.append({"kind": "outro", "key": "outro", "headline": "Outro", "text": doc["outro"]})
    return parts


def _find_existing(out_base: Path) -> Path | None:
    """Return an already-generated audio file for ``out_base``, if any."""
    for ext in _AUDIO_EXTS:
        candidate = out_base.with_suffix(f".{ext}")
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _synthesize(providers: list[TTSProvider], text: str, out_base: Path, cfg: Config) -> tuple[Path | None, str | None]:
    """Try each provider in order; return ``(path, provider_name)`` on success."""
    for provider in providers:
        target = out_base.with_suffix(f".{provider.extension}")
        path = provider.synthesize(text, target, cfg)
        if path is not None:
            return path, provider.name
    return None, None


def run_voice(
    cfg: Config,
    *,
    provider: TTSProvider | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate the voiceover for today's script (Stage 5).

    Args:
        cfg: Full configuration.
        provider: Optional provider instance to use instead of the configured
            order (used for testing and for ``voice-test``).
        force: Re-synthesize even when audio files already exist.

    Returns:
        A summary dict with counts, the active provider/voice, total duration
        and the manifest path.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    doc, script_path = _load_script(cfg, date_str)

    summary: dict[str, Any] = {
        "date": date_str,
        "script_path": script_path,
        "parts": 0,
        "generated": 0,
        "reused": 0,
        "failed": 0,
        "provider": None,
        "voice": cfg.voice.voice,
        "total_seconds": 0.0,
        "manifest_path": None,
        "ok": False,
    }
    if doc is None:
        logger.warning("No script for %s — run `python main.py script` first.", date_str)
        return summary

    providers = [provider] if provider is not None else resolve_providers(cfg.voice.provider_order, cfg)
    if not providers:
        logger.error(
            "No TTS provider is available. Install edge-tts "
            "(`pip install -r requirements.txt`)."
        )
        return summary
    summary["provider"] = providers[0].name

    audio_dir = cfg.data_dir / "audio" / date_str
    audio_dir.mkdir(parents=True, exist_ok=True)

    manifest_parts: list[dict[str, Any]] = []
    total = 0.0
    for part in build_parts(doc):
        text = normalize_for_speech(part["text"], cfg.voice.pronunciation)
        if not text:
            continue

        out_base = audio_dir / part["key"]
        path = None if force else _find_existing(out_base)
        used_provider: str | None = None
        if path is None:
            path, used_provider = _synthesize(providers, text, out_base, cfg)

        if path is None:
            summary["failed"] += 1
            logger.warning("No audio produced for part '%s'", part["key"])
            continue

        duration = audio_duration(path)
        total += duration or 0.0
        summary["parts"] += 1
        if used_provider:
            summary["generated"] += 1
        else:
            summary["reused"] += 1

        manifest_parts.append(
            {
                "kind": part["kind"],
                "key": part["key"],
                "story_id": part.get("story_id"),
                "headline": part.get("headline", ""),
                "text": text,
                "path": str(path),
                "duration": duration,
                "provider": used_provider or summary["provider"],
            }
        )

    if not manifest_parts:
        logger.warning("No audio was produced for today's script.")
        return summary

    manifest = {
        "date": date_str,
        "provider": summary["provider"],
        "voice": cfg.voice.voice,
        "total_seconds": round(total, 2),
        "pauses": {
            "sentence_ms": cfg.voice.sentence_pause_ms,
            "story_ms": cfg.voice.story_pause_ms,
        },
        "parts": manifest_parts,
    }
    manifest_path = audio_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    summary["total_seconds"] = round(total, 2)
    summary["manifest_path"] = manifest_path
    summary["ok"] = summary["failed"] == 0
    logger.info(
        "Voiceover ready: %d parts, %.1fs (%s)",
        summary["parts"],
        summary["total_seconds"],
        summary["provider"],
    )
    return summary
