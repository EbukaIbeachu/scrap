"""TTS provider: ``edge-tts`` (Microsoft neural voices).

Free, no API key, and includes Nigerian English voices such as
``en-NG-AbeoNeural`` and ``en-NG-EzinneNeural``. It needs an internet
connection and is an unofficial route to Microsoft's service, so the offline
``kokoro``/``piper`` providers act as fallbacks.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..config import Config
from .base import TTSProvider, VoiceInfo

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-NG-AbeoNeural"


class EdgeTTS(TTSProvider):
    """Microsoft neural voices via the ``edge-tts`` package."""

    name = "edge"
    extension = "mp3"

    def available(self, cfg: Config) -> bool:
        """Return True when the ``edge-tts`` package is importable."""
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False
        return True

    def synthesize(self, text: str, out_path: Path, cfg: Config) -> Path | None:
        """Synthesize ``text`` to an MP3 file using the configured voice."""
        import edge_tts
        import os

        out_path = out_path.with_suffix(".mp3")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        voice = cfg.voice.voice or DEFAULT_VOICE
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None

        async def _save() -> None:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=cfg.voice.rate or "+0%",
                pitch=cfg.voice.pitch or "+0Hz",
                volume=cfg.voice.volume or "+0%",
                proxy=proxy,
            )
            await communicate.save(str(out_path))

        try:
            asyncio.run(_save())
        except Exception as exc:  # network/voice errors must not crash the run
            logger.warning("edge-tts synthesis failed (voice=%s): %s", voice, exc)
            return None
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path
        return None

    def list_voices(self) -> list[VoiceInfo]:
        """List every voice edge-tts offers (requires internet)."""
        import edge_tts

        try:
            raw = asyncio.run(edge_tts.list_voices())
        except Exception as exc:
            logger.warning("Could not list edge-tts voices: %s", exc)
            return []
        return [
            VoiceInfo(
                id=str(v.get("ShortName", "")),
                name=str(v.get("FriendlyName", v.get("ShortName", ""))),
                provider=self.name,
                language=str(v.get("Locale", "")),
                gender=str(v.get("Gender", "")),
            )
            for v in raw
        ]
