"""Common interface for text-to-speech providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..context import Ctx


class TTSProvider(ABC):
    """A voice provider. ``synthesize`` writes any audio file FFmpeg can read."""

    name = "base"
    conf_key = ""  # section name under ``tts`` in config.yaml
    native_speed = False  # True if the provider applies tts.speed itself

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.conf: dict = ctx.get(f"tts.{self.conf_key}", {}) or {}
        self.speed = float(ctx.get("tts.speed", 1.0))
        self.pitch_hz = int(ctx.get("tts.pitch_hz", 0))

    def available(self) -> tuple[bool, str]:
        """(ready, reason-if-not)."""
        return True, ""

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> Path:
        """Speak ``text`` into ``out_path`` and return the path."""

    def list_voices(self, locale: str | None = None) -> list[str]:
        return []
