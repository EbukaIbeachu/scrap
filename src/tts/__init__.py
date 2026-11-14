"""Voice generation with provider fallback, natural pauses and speed handling."""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

from ..context import Ctx
from ..utils import UserError, log, media_duration, run_ffmpeg
from .base import TTSProvider
from .edge_tts_provider import EdgeTTS
from .elevenlabs_tts import ElevenLabsTTS
from .kokoro_tts import KokoroTTS
from .mock_tts import MockTTS
from .openai_tts import OpenAITTS
from .own_voice_tts import OwnVoiceTTS
from .piper_tts import PiperTTS
from .text_prep import group_sentences, prepare_text, split_sentences

REGISTRY: dict[str, type[TTSProvider]] = {
    "edge-tts": EdgeTTS, "edge": EdgeTTS, "kokoro": KokoroTTS, "piper": PiperTTS,
    "elevenlabs": ElevenLabsTTS, "openai": OpenAITTS, "own_voice": OwnVoiceTTS, "mock": MockTTS,
}
SAMPLE_RATE = 24000


def provider_order(ctx: Ctx) -> list[str]:
    """Provider names to try, in order. --dry-run always uses the silent mock voice."""
    if ctx.dry_run:
        return ["mock"]
    return [str(n).lower() for n in ctx.get("tts.providers", ["edge-tts"])]


def build_provider(ctx: Ctx, name: str) -> TTSProvider:
    cls = REGISTRY.get(name.lower())
    if cls is None:
        raise UserError(f"Unknown voice provider '{name}'. Choose from: {', '.join(sorted(REGISTRY))}")
    return cls(ctx)


def _normalize(src: Path, dst: Path, speed: float, apply_speed: bool) -> None:
    """Convert any audio to 24 kHz mono 16-bit WAV (optionally changing tempo)."""
    args: list[object] = ["-i", src, "-ar", SAMPLE_RATE, "-ac", 1, "-sample_fmt", "s16"]
    if apply_speed and abs(speed - 1.0) > 0.01:
        args += ["-af", f"atempo={min(max(speed, 0.5), 2.0):.3f}"]
    run_ffmpeg([*args, dst])


def _render(ctx: Ctx, provider: TTSProvider, chunks: list[str], out_wav: Path) -> None:
    pause = int(ctx.get("tts.sentence_pause_ms", 280))
    silence = b"\x00\x00" * int(SAMPLE_RATE * pause / 1000)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        wavs: list[Path] = []
        for i, chunk in enumerate(chunks):
            raw = provider.synthesize(chunk, tmp / f"p{i}")
            norm = tmp / f"n{i}.wav"
            _normalize(raw, norm, provider.speed, apply_speed=not provider.native_speed)
            wavs.append(norm)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_wav), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            for i, w in enumerate(wavs):
                with wave.open(str(w), "rb") as part:
                    out.writeframes(part.readframes(part.getnframes()))
                if i < len(wavs) - 1:
                    out.writeframes(silence)


def synthesize_text(ctx: Ctx, text: str, out_wav: Path, order: list[str]) -> tuple[float, str]:
    """Speak ``text`` into ``out_wav``. Tries providers in ``order``; the one that works moves to the front.

    Returns (duration_seconds, provider_name).
    """
    clean = prepare_text(text, ctx.get("tts.pronunciations"))
    if not clean:
        raise UserError("Nothing to narrate: the text is empty after cleaning.")
    errors: list[str] = []
    for name in list(order):
        provider = build_provider(ctx, name)
        ok, why = provider.available()
        if not ok:
            errors.append(f"{name}: {why}")
            continue
        max_chars = getattr(provider, "max_chars", 240)
        chunks = group_sentences(split_sentences(clean), max_chars=max_chars)
        try:
            _render(ctx, provider, chunks, out_wav)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            log.warning("Voice provider '%s' failed (%s); trying the next one", name, exc)
            continue
        if order[0] != name:
            order.remove(name)
            order.insert(0, name)
        return media_duration(out_wav), provider.name
    raise UserError("No voice provider could speak the text:\n  " + "\n  ".join(errors))
