"""OpenAI text-to-speech (optional, paid). Key in OPENAI_API_KEY."""
from __future__ import annotations

import os
from pathlib import Path

import requests

from .base import TTSProvider


class OpenAITTS(TTSProvider):
    name = "openai"
    conf_key = "openai"

    def available(self) -> tuple[bool, str]:
        if not os.getenv("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY is not set"
        return True, ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": self.conf.get("model", "gpt-4o-mini-tts"),
                "voice": self.conf.get("voice", "alloy"),
                "input": text,
                "response_format": "mp3",
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI TTS HTTP {resp.status_code}: {resp.text[:200]}")
        out_path = out_path.with_suffix(".mp3")
        out_path.write_bytes(resp.content)
        return out_path
