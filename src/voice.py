"""Stage 5: one audio file per script segment (cached by text + voice settings)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .context import Ctx
from .script import load_script
from .tts import REGISTRY, build_provider, provider_order, synthesize_text
from .utils import UserError, digest, log, open_path


def audio_paths(ctx: Ctx, index: int) -> tuple[Path, Path]:
    folder = ctx.subdir("audio", ctx.date)
    return folder / f"seg_{index:03d}.wav", folder / f"seg_{index:03d}.json"


def _settings_key(ctx: Ctx, order: list[str]) -> str:
    return digest(json.dumps(ctx.get("tts", {}), sort_keys=True, default=str), ctx.dry_run, order[0] if order else "")


def run_voice(ctx: Ctx) -> int:
    """Generate (or reuse) the voiceover for every segment. Returns the number generated."""
    segments = load_script(ctx)
    order = provider_order(ctx)
    key = _settings_key(ctx, order)
    made = 0
    for i, seg in enumerate(segments):
        wav, meta_path = audio_paths(ctx, i)
        want = digest(seg["narration"], key)
        if wav.exists() and meta_path.exists() and not ctx.force:
            if json.loads(meta_path.read_text(encoding="utf-8")).get("hash") == want:
                continue
        duration, provider = synthesize_text(ctx, seg["narration"], wav, order)
        meta_path.write_text(
            json.dumps({"hash": want, "duration": duration, "provider": provider}), encoding="utf-8"
        )
        made += 1
        log.info("Voice %d/%d: %.1fs (%s)", i + 1, len(segments), duration, provider)
    log.info("Voiceover ready for %d segments (%d new)", len(segments), made)
    return made


def load_audio_meta(ctx: Ctx, index: int) -> dict:
    wav, meta_path = audio_paths(ctx, index)
    if not wav.exists() or not meta_path.exists():
        raise UserError("Voiceover is missing. Run:  python main.py voice")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["path"] = str(wav)
    return meta


def voice_test(ctx: Ctx, text: str, provider_name: str | None = None) -> Path:
    """Speak a sample sentence so you can compare voices quickly."""
    order = [provider_name] if provider_name else provider_order(ctx)
    out = ctx.subdir("audio") / "voice_test.wav"
    duration, used = synthesize_text(ctx, text, out, order)
    log.info("Saved %.1fs sample made with '%s': %s", duration, used, out)
    if shutil.which("ffplay"):
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(out)])
    else:
        open_path(out)
    return out


def list_voices(ctx: Ctx, provider_name: str | None, locale: str | None) -> list[str]:
    name = provider_name or provider_order(ctx)[0]
    if name not in REGISTRY:
        raise UserError(f"Unknown provider '{name}'. Choose from: {', '.join(sorted(REGISTRY))}")
    provider = build_provider(ctx, name)
    ok, why = provider.available()
    if not ok:
        raise UserError(f"{name} is not ready: {why}")
    return provider.list_voices(locale)
