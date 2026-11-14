"""Silent placeholder voice for --dry-run and tests (no network, no cost)."""
from __future__ import annotations

import wave
from pathlib import Path

from .base import TTSProvider


class MockTTS(TTSProvider):
    name = "mock"
    conf_key = "mock"
    native_speed = True

    def synthesize(self, text: str, out_path: Path) -> Path:
        seconds = max(0.8, len(text.split()) / 2.6)  # roughly 156 words per minute
        rate = 24000
        out_path = out_path.with_suffix(".wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"\x00\x00" * int(rate * seconds))
        return out_path

    def list_voices(self, locale: str | None = None) -> list[str]:
        return ["silent-placeholder"]
