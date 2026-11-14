"""SQLite storage for stories, images, public-figure photos and daily selections."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    outlet        TEXT NOT NULL,
    url           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    summary       TEXT,
    text          TEXT,
    text_quality  TEXT,            -- 'full' or 'summary_only'
    published     TEXT,            -- ISO 8601 UTC
    fetched_at    TEXT NOT NULL,
    image_urls    TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id     INTEGER NOT NULL REFERENCES stories(id),
    kind         TEXT NOT NULL,    -- article | stock | ai | card
    path         TEXT NOT NULL,
    source_url   TEXT,
    license_note TEXT,
    credit       TEXT,
    UNIQUE(story_id, path)
);
CREATE TABLE IF NOT EXISTS people_photos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id      INTEGER NOT NULL REFERENCES stories(id),
    name          TEXT NOT NULL,
    role          TEXT,
    path          TEXT,            -- NULL when no safe photo was found
    source_url    TEXT,
    license       TEXT,
    credit        TEXT,
    confidence    REAL NOT NULL DEFAULT 0,
    commercial_ok INTEGER NOT NULL DEFAULT 0,
    approved      INTEGER,         -- NULL = undecided, 1 = approved, 0 = rejected
    note          TEXT,
    UNIQUE(story_id, name)
);
CREATE TABLE IF NOT EXISTS selections (
    date     TEXT NOT NULL,
    story_id INTEGER NOT NULL REFERENCES stories(id),
    rank     INTEGER NOT NULL,
    PRIMARY KEY (date, story_id)
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    """Thin wrapper over sqlite3 returning plain dicts."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _d(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    # ------------------------------------------------------------------ stories
    def story_exists(self, url: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM stories WHERE url = ?", (url,))
        return cur.fetchone() is not None

    def add_story(
        self,
        outlet: str,
        url: str,
        title: str,
        summary: str = "",
        text: str = "",
        text_quality: str = "summary_only",
        published: str | None = None,
        image_urls: Iterable[str] = (),
    ) -> int | None:
        """Insert a story. Returns its id, or None if the URL was already stored."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO stories
               (outlet, url, title, summary, text, text_quality, published, fetched_at, image_urls)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (outlet, url, title, summary, text, text_quality, published, utc_now_iso(),
             json.dumps(list(dict.fromkeys(image_urls)))),
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def get_story(self, story_id: int) -> dict | None:
        story = self._d(self.conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone())
        if story:
            story["image_urls"] = json.loads(story.get("image_urls") or "[]")
        return story

    def recent_stories(self, since_iso: str) -> list[dict]:
        """Stories published (or fetched, when no date) at or after ``since_iso``, newest first."""
        rows = self.conn.execute(
            """SELECT *, COALESCE(published, fetched_at) AS ts FROM stories
               WHERE COALESCE(published, fetched_at) >= ? ORDER BY ts DESC""",
            (since_iso,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["image_urls"] = json.loads(d.get("image_urls") or "[]")
            out.append(d)
        return out

    def count_stories(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]

    # ------------------------------------------------------------------ selections
    def save_selection(self, date: str, story_ids: list[int]) -> None:
        self.conn.execute("DELETE FROM selections WHERE date = ?", (date,))
        self.conn.executemany(
            "INSERT INTO selections (date, story_id, rank) VALUES (?,?,?)",
            [(date, sid, i) for i, sid in enumerate(story_ids)],
        )
        self.conn.commit()

    def get_selection(self, date: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT story_id FROM selections WHERE date = ? ORDER BY rank", (date,)
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ images
    def add_image(
        self,
        story_id: int,
        kind: str,
        path: str,
        source_url: str = "",
        license_note: str = "",
        credit: str = "",
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO images (story_id, kind, path, source_url, license_note, credit)
               VALUES (?,?,?,?,?,?)""",
            (story_id, kind, path, source_url, license_note, credit),
        )
        self.conn.commit()

    def images_for(self, story_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM images WHERE story_id = ? ORDER BY id", (story_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_images(self, story_id: int) -> None:
        self.conn.execute("DELETE FROM images WHERE story_id = ?", (story_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ people photos
    def upsert_person(self, story_id: int, name: str, role: str = "", **fields: Any) -> None:
        """Create or update a public-figure record. Keeps any existing approval decision."""
        existing = self.conn.execute(
            "SELECT id FROM people_photos WHERE story_id = ? AND name = ?", (story_id, name)
        ).fetchone()
        allowed = ("path", "source_url", "license", "credit", "confidence", "commercial_ok", "note")
        data = {k: v for k, v in fields.items() if k in allowed}
        if existing is None:
            cols = ["story_id", "name", "role", *data.keys()]
            vals = [story_id, name, role, *data.values()]
            self.conn.execute(
                f"INSERT INTO people_photos ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
            )
        else:
            sets = ["role = ?", *[f"{k} = ?" for k in data]]
            self.conn.execute(
                f"UPDATE people_photos SET {', '.join(sets)} WHERE id = ?",
                [role, *data.values(), existing[0]],
            )
        self.conn.commit()

    def person_known(self, story_id: int, name: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM people_photos WHERE story_id = ? AND name = ?", (story_id, name)
        )
        return cur.fetchone() is not None

    def people_for(self, story_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM people_photos WHERE story_id = ? ORDER BY id", (story_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def set_person_approved(self, person_id: int, approved: bool | None) -> None:
        val = None if approved is None else int(approved)
        self.conn.execute("UPDATE people_photos SET approved = ? WHERE id = ?", (val, person_id))
        self.conn.commit()

    def pending_people(self, story_ids: list[int]) -> list[dict]:
        """Photos found but not yet approved or rejected."""
        if not story_ids:
            return []
        q = ",".join("?" * len(story_ids))
        rows = self.conn.execute(
            f"SELECT * FROM people_photos WHERE story_id IN ({q}) AND path IS NOT NULL AND approved IS NULL",
            story_ids,
        ).fetchall()
        return [dict(r) for r in rows]
