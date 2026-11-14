"""Speak in YOUR voice using the open-source Coqui XTTS model (pip install TTS torch).

Put a clean 10-30 second recording of your own voice (wav or mp3, no background noise) in
``data/voice_samples/``. Only use your own voice, or a voice you have permission to use.
Check the XTTS licence before using results commercially. Not exercised by the automated
tests: the first run downloads a model of a few GB, and it is slow without a GPU.

If ``tts.own_voice.model_dir`` holds a complete XTTS folder (config.json, model.pth,
vocab.json, speakers_xtts.pth) that copy is used instead of downloading from Hugging Face.
"""
from __future__ import annotations

from pathlib import Path

from .base import TTSProvider


def read_wav_with_soundfile() -> None:
    """Let XTTS read the reference clip without ``torchcodec``.

    XTTS calls ``torchaudio.load`` on the reference clip. On this Python that goes through
    torchcodec, which needs FFmpeg's *shared* libraries, and a normal Windows FFmpeg install
    only ships the executables. Every clip this tool uses is a plain WAV, so read it with
    soundfile instead and leave the rest of torchaudio untouched.
    """
    import soundfile as sf
    import torch
    import torchaudio

    def load(path, *args, **kwargs):
        data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), int(sample_rate)

    torchaudio.load = load


class OwnVoiceTTS(TTSProvider):
    name = "own_voice"
    conf_key = "own_voice"
    _model = None
    max_chars = 240  # XTTS works best with short chunks

    def _sample(self) -> Path | None:
        folder = self.ctx.resolve_path(self.conf.get("sample_dir", "voice_samples"))
        if not folder.exists():
            return None
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".flac", ".m4a"))
        return files[0] if files else None

    def _local_model_dir(self) -> Path | None:
        """A complete XTTS model folder inside data/, used instead of downloading from the hub."""
        folder = self.ctx.resolve_path(self.conf.get("model_dir", "models/xtts_v2"))
        needed = ("config.json", "model.pth", "vocab.json", "speakers_xtts.pth")
        if all((folder / name).exists() for name in needed):
            return folder
        return None

    def available(self) -> tuple[bool, str]:
        try:
            from TTS.api import TTS  # noqa: F401
        except ImportError:
            return False, "the 'TTS' package is not installed (pip install TTS torch)"
        if self._sample() is None:
            return False, "put a recording of your voice in data/voice_samples/"
        return True, ""

    def synthesize(self, text: str, out_path: Path) -> Path:
        import torch
        from TTS.api import TTS

        read_wav_with_soundfile()
        if OwnVoiceTTS._model is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            local = self._local_model_dir()
            if local:
                OwnVoiceTTS._model = TTS(model_path=str(local / "model.pth"),
                                         config_path=str(local / "config.json")).to(device)
            else:
                OwnVoiceTTS._model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
        out_path = out_path.with_suffix(".wav")
        OwnVoiceTTS._model.tts_to_file(
            text=text, speaker_wav=str(self._sample()), language=self.conf.get("language", "en"),
            file_path=str(out_path),
        )
        return out_path
