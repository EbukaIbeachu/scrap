"""Microsoft Edge neural voices via the free ``edge-tts`` package (needs internet).

Unofficial route to Microsoft's online voices: it can change or stop working without notice,
which is why the config falls back to offline voices. Nigerian English voices include
en-NG-EzinneNeural (female) and en-NG-AbeoNeural (male).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from .base import TTSProvider


class EdgeTTS(TTSProvider):
    name = "edge-tts"
    conf_key = "edge_tts"
    native_speed = True

    def available(self) -> tuple[bool, str]:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False, "the 'edge-tts' package is not installed (pip install edge-tts)"
        return True, ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        import edge_tts

        voice = self.conf.get("voice", "en-NG-EzinneNeural")
        rate = f"{round((self.speed - 1) * 100):+d}%"
        pitch = f"{self.pitch_hz:+d}Hz"
        out_path = out_path.with_suffix(".mp3")
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or None

        async def go() -> None:
            await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, proxy=proxy).save(str(out_path))

        last: Exception | None = None
        for attempt in range(3):  # transient network errors are common
            try:
                asyncio.run(go())
                if out_path.exists() and out_path.stat().st_size > 0:
                    return out_path
            except Exception as exc:
                last = exc
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"edge-tts failed after 3 tries: {last}")

    def list_voices(self, locale: str | None = None) -> list[str]:
        import edge_tts

        voices = asyncio.run(edge_tts.list_voices())
        rows = [
            f"{v['ShortName']:<32} {v.get('Gender', ''):<7} {v.get('Locale', '')}"
            for v in voices
            if not locale or v.get("Locale", "").lower().startswith(locale.lower())
        ]
        return sorted(rows)
