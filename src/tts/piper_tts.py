"""Piper: lightweight offline voice (pip install piper-tts, plus a downloaded .onnx voice model).

Not exercised by this project's automated tests; try it with ``python main.py voice-test``.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import TTSProvider


class PiperTTS(TTSProvider):
    name = "piper"
    conf_key = "piper"

    def _model(self) -> Path:
        return self.ctx.resolve_path(self.conf.get("model_path", "")) if self.conf.get("model_path") else Path("")

    def available(self) -> tuple[bool, str]:
        if not shutil.which(self.conf.get("binary", "piper")):
            return False, "the 'piper' program is not installed (pip install piper-tts)"
        if not self.conf.get("model_path") or not self._model().exists():
            return False, "set tts.piper.model_path to a downloaded Piper .onnx voice file"
        return True, ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        out_path = out_path.with_suffix(".wav")
        res = subprocess.run(
            [self.conf.get("binary", "piper"), "--model", str(self._model()), "--output_file", str(out_path)],
            input=text, text=True, capture_output=True, encoding="utf-8", errors="replace",
        )
        if res.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"piper failed: {res.stderr[-300:]}")
        return out_path
