"""Run context shared by every stage (config, paths, date, counters)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Ctx:
    cfg: dict
    root: Path
    date: str
    dry_run: bool = False
    force: bool = False
    claude_calls: int = 0
    _db: Any = field(default=None, repr=False)

    # ---- config access
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.cfg
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    # ---- paths
    @property
    def data_dir(self) -> Path:
        p = Path(self.get("general.data_dir", "data"))
        p = p if p.is_absolute() else self.root / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def subdir(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolve_path(self, value: str) -> Path:
        """Resolve a config path: absolute as-is, otherwise relative to the data folder."""
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.data_dir / p

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    # ---- database
    @property
    def db(self):
        if self._db is None:
            from .db import Database

            self._db = Database(self.data_dir / "news.db")
        return self._db

    # ---- video geometry
    @property
    def video_size(self) -> tuple[int, int]:
        """(width, height) in pixels. ``video.height`` is the short side."""
        short = int(self.get("video.height", 720))
        short -= short % 2
        long = int(round(short * 16 / 9 / 2)) * 2
        if str(self.get("video.aspect", "landscape")).lower().startswith("p"):
            return short, long
        return long, short

    @property
    def work_size(self) -> tuple[int, int]:
        """Size stored for prepared images (1.5x the video, leaves room for zoom)."""
        w, h = self.video_size
        return int(w * 1.5) // 2 * 2, int(h * 1.5) // 2 * 2

    @property
    def monetization_safe(self) -> bool:
        return bool(self.get("images.monetization_safe", False))
