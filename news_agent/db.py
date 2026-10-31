"""Trạng thái agent trong SQLite: URL đã quét và bản nháp chờ duyệt."""
from __future__ import annotations
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_urls (
    url TEXT PRIMARY KEY,
    seen_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | published | rejected | superseded | failed
    title TEXT,
    data TEXT,                               -- JSON bài viết đã viết lại
    tg_message_id INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status);
"""


@contextmanager
def connect():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_seen(url: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM seen_urls WHERE url=?", (url,)).fetchone() is not None


def mark_seen(url: str) -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO seen_urls(url, seen_at) VALUES(?, ?)", (url, int(time.time())))


def add_draft(slug: str, source_url: str, title: str, data: dict) -> int:
    now = int(time.time())
    with connect() as c:
        cur = c.execute(
            "INSERT INTO drafts(slug, source_url, title, data, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (slug, source_url, title, json.dumps(data, ensure_ascii=False), now, now),
        )
        return int(cur.lastrowid)


def get_draft(draft_id: int):
    with connect() as c:
        row = c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None


def set_status(draft_id: int, status: str, expect: str | None = None) -> bool:
    """Đổi trạng thái; nếu có expect thì chỉ đổi khi trạng thái hiện tại khớp (chống bấm 2 lần)."""
    with connect() as c:
        if expect:
            cur = c.execute(
                "UPDATE drafts SET status=?, updated_at=? WHERE id=? AND status=?",
                (status, int(time.time()), draft_id, expect),
            )
        else:
            cur = c.execute("UPDATE drafts SET status=?, updated_at=? WHERE id=?", (status, int(time.time()), draft_id))
        return cur.rowcount == 1


def set_message_id(draft_id: int, message_id: int) -> None:
    with connect() as c:
        c.execute("UPDATE drafts SET tg_message_id=? WHERE id=?", (message_id, draft_id))


def pending_slugs() -> set[str]:
    with connect() as c:
        return {r["slug"] for r in c.execute("SELECT slug FROM drafts WHERE status='pending'")}


def expired_pending(ttl_days: int):
    cutoff = int(time.time()) - ttl_days * 86400
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM drafts WHERE status='pending' AND created_at<?", (cutoff,))]


def get_kv(key: str, default: str = "") -> str:
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


def set_kv(key: str, value: str) -> None:
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (key, value))
