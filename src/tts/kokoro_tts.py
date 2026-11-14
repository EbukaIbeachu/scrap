"""Kokoro: open-source, natural-sounding voice that runs offline (pip install kokoro soundfile).

Not exercised by this project's automated tests; try it with ``python main.py voice-test``.
"""
from __future__ import annotations

from pathlib import Path

from .base import TTSProvider


class KokoroTTS(TTSProvider):
    name = "kokoro"
    conf_key = "kokoro"
    native_speed = True
    _pipeline = None

    def available(self) -> tuple[bool, str]:
        try:
            import kokoro  # noqa: F401
            import soundfile  # noqa: F401
        except ImportError:
            return False, "kokoro / soundfile are not installed (pip install kokoro soundfile)"
        return True, ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        if KokoroTTS._pipeline is None:
            KokoroTTS._pipeline = KPipeline(lang_code=self.conf.get("lang_code", "a"))
        pieces = []
        for _gs, _ps, audio in KokoroTTS._pipeline(text, voice=self.conf.get("voice", "af_heart"), speed=self.speed):
            pieces.append(audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio))
        if not pieces:
            raise RuntimeError("Kokoro produced no audio")
        out_path = out_path.with_suffix(".wav")
        sf.write(str(out_path), np.concatenate(pieces), 24000)
        return out_path

    def list_voices(self, locale: str | None = None) -> list[str]:
        return ["af_heart", "af_bella", "af_sarah", "am_adam", "am_michael", "bf_emma", "bm_george"]
