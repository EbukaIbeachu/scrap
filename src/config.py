"""Configuration loading. Defaults live here; ``config.yaml`` overrides them."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .sections import DEFAULT_HEADER_TEMPLATE

DEFAULTS: dict[str, Any] = {
    "general": {
        "language": "en",
        "timezone": "Africa/Lagos",
        "user_agent": "NewsHighlightsPersonal/1.0 (personal, non-commercial use)",
        "request_delay_seconds": 2.0,
        "request_timeout": 20,
        "data_dir": "data",
        "allow_file_urls": False,  # only used by the automated tests
        "max_claude_calls_per_run": 20,
    },
    "claude": {"model": "claude-sonnet-5-5", "max_tokens": 4000, "temperature": 0.3},
    "outlets": [],
    "news_apis": [],
    "collector": {
        "max_stories_per_outlet": 5,
        "min_article_chars": 600,
        "fetch_full_text": True,
        "respect_robots": True,
        "lookback_hours": 48,
    },
    "selection": {
        "top_n": 5,
        "max_per_outlet": 2,
        "keywords_include": [],
        "keywords_exclude": [],
        "prefer_full_text": True,
    },
    # Named video sections. When enabled, selection follows the rules below instead of
    # "selection.top_n", and the script gets a spoken section header before each group.
    "sections": {"enabled": False, "header_template": DEFAULT_HEADER_TEMPLATE, "rules": []},
    "images": {
        "image_mode": "auto",  # article | stock | ai | cards | auto
        "auto_order": ["article", "stock", "cards"],
        "images_per_story": 2,
        "min_width": 500,
        "min_height": 300,
        "monetization_safe": False,
        "pexels_api_key_env": "PEXELS_API_KEY",
        "pixabay_api_key_env": "PIXABAY_API_KEY",
        "ai_provider": "",
    },
    "people": {
        "enabled": True,
        "max_per_story": 2,
        "require_approval": True,
        "auto_use_min_confidence": 0.85,
        "manual_photos_dir": "people_photos",
        "manual_commercial_ok": False,
        "wikipedia_lang": "en",
        "search_api": {"enabled": False, "key_env": "GOOGLE_CSE_KEY", "cx_env": "GOOGLE_CSE_CX"},
    },
    "script": {
        "words_per_story": 90,
        "intro_words": 30,
        "outro_words": 25,
        "tone": "neutral, clear, conversational news-reader",
    },
    "tts": {
        "providers": ["edge-tts", "kokoro", "piper"],
        "speed": 1.0,
        "pitch_hz": 0,
        "sentence_pause_ms": 280,
        "edge_tts": {"voice": "en-NG-EzinneNeural"},
        "kokoro": {"voice": "af_heart", "lang_code": "a"},
        "piper": {"binary": "piper", "model_path": ""},
        "elevenlabs": {"voice_id": "", "model_id": "eleven_multilingual_v2"},
        "openai": {"model": "gpt-4o-mini-tts", "voice": "alloy"},
        "own_voice": {"sample_dir": "voice_samples", "language": "en", "model_dir": "models/xtts_v2"},
        "pronunciations": {},
    },
    "video": {
        "aspect": "landscape",  # landscape (16:9) | portrait (9:16)
        "height": 720,  # the SHORT side in pixels: 720 -> 1280x720 or 720x1280
        "fps": 25,
        "image_transition_seconds": 0.6,
        "segment_pad_seconds": 0.5,
        "zoom_amount": 0.12,
        "crf": 20,
        "preset": "veryfast",
        "music_dir": "music",
        "music_volume": 0.07,
        "brand": {
            "name": "News Highlights",
            "primary_color": "#0B3D91",
            "accent_color": "#F2A900",
            "text_color": "#FFFFFF",
            "font_path": "",
            "logo_path": "",
            "intro_title": "Today's News Highlights",
            "outro_text": "Thanks for watching",
        },
    },
    "export": {
        "target_mb": 15.0,  # decimal MB; WhatsApp's video limit is about 16 MB
        "audio_kbps": 96,
        "min_video_kbps": 350,
        "max_video_kbps": 2500,
        "preset": "medium",
    },
    "download": {"download_dir": ""},
}


def deep_merge(base: dict, override: dict) -> dict:
    """Return ``base`` updated with ``override`` (recursively for dicts)."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: Path) -> dict:
    """Load ``config.yaml`` (if present) on top of the defaults."""
    user: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
    return deep_merge(DEFAULTS, user)
