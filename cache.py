import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple


class CacheDB:
    def __init__(self, path: str = "cache.sqlite") -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fiction (
                    fiction_slug TEXT PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    chapters_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chapter (
                    fiction_slug TEXT NOT NULL,
                    chapter_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    text_raw TEXT NOT NULL,
                    text_html TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    fetched_at REAL NOT NULL,
                    PRIMARY KEY (fiction_slug, chapter_id)
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chapter_fiction ON chapter (fiction_slug);")

    # ----- Fiction chapters list -----

    def get_fiction(self, fiction_slug: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fiction_slug, fetched_at, chapters_json FROM fiction WHERE fiction_slug = ?",
                (fiction_slug,),
            ).fetchone()
            if not row:
                return None
            return {
                "fiction_slug": row["fiction_slug"],
                "fetched_at": float(row["fetched_at"]),
                "chapters": json.loads(row["chapters_json"]),
            }

    def put_fiction(self, fiction_slug: str, chapters: List[dict]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fiction (fiction_slug, fetched_at, chapters_json)
                VALUES (?, ?, ?)
                ON CONFLICT(fiction_slug) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    chapters_json=excluded.chapters_json
                """,
                (fiction_slug, time.time(), json.dumps(chapters, ensure_ascii=False)),
            )

    # ----- Per-chapter content -----

    def get_chapter(self, fiction_slug: str, chapter_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT fiction_slug, chapter_id, title, text_raw, text_html, content_html, fetched_at
                FROM chapter
                WHERE fiction_slug = ? AND chapter_id = ?
                """,
                (fiction_slug, int(chapter_id)),
            ).fetchone()
            if not row:
                return None
            return {
                "fiction_slug": row["fiction_slug"],
                "chapter_id": int(row["chapter_id"]),
                "title": row["title"],
                "text_raw": row["text_raw"],
                "text_html": row["text_html"],
                "content_html": row["content_html"],
                "fetched_at": float(row["fetched_at"]),
            }

    def put_chapter(
        self,
        fiction_slug: str,
        chapter_id: int,
        title: str,
        text_raw: str,
        text_html: str,
        content_html: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chapter (fiction_slug, chapter_id, title, text_raw, text_html, content_html, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fiction_slug, chapter_id) DO UPDATE SET
                    title=excluded.title,
                    text_raw=excluded.text_raw,
                    text_html=excluded.text_html,
                    content_html=excluded.content_html,
                    fetched_at=excluded.fetched_at
                """,
                (fiction_slug, int(chapter_id), title, text_raw, text_html, content_html, time.time()),
            )
