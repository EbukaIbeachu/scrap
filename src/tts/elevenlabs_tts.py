"""ElevenLabs (optional, paid / limited free credits). Key in ELEVENLABS_API_KEY."""
from __future__ import annotations

import os
from pathlib import Path

import requests

from .base import TTSProvider


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    conf_key = "elevenlabs"

    def available(self) -> tuple[bool, str]:
        if not os.getenv("ELEVENLABS_API_KEY"):
            return False, "ELEVENLABS_API_KEY is not set"
        if not self.conf.get("voice_id"):
            return False, "set tts.elevenlabs.voice_id in config.yaml"
        return True, ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.conf['voice_id']}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Accept": "audio/mpeg"},
            json={"text": text, "model_id": self.conf.get("model_id", "eleven_multilingual_v2")},
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:200]}")
        out_path = out_path.with_suffix(".mp3")
        out_path.write_bytes(resp.content)
        return out_path
