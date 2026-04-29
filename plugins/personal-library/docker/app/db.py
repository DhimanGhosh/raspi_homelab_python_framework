from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from app.config import DB_PATH, DEFAULT_SCORE_SETTINGS

# ── Schema & migrations ────────────────────────────────────────────────────────

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, author TEXT DEFAULT '', isbn TEXT DEFAULT '',
    genre TEXT DEFAULT '', subgenres TEXT DEFAULT '', description TEXT DEFAULT '',
    language TEXT DEFAULT '', published_year TEXT DEFAULT '', page_count INTEGER DEFAULT 0,
    publisher TEXT DEFAULT '', info_link TEXT DEFAULT '', cover_url TEXT DEFAULT '',
    buy_link TEXT DEFAULT '', mood TEXT DEFAULT '', english_label TEXT DEFAULT 'Moderate',
    english_ease_score INTEGER DEFAULT 3, india_set TEXT DEFAULT 'Unknown',
    wow_score INTEGER DEFAULT 3, emotional_score INTEGER DEFAULT 3,
    sadness_score INTEGER DEFAULT 2, realism_score INTEGER DEFAULT 3,
    personalized_score REAL DEFAULT 0, rating REAL DEFAULT 0,
    status TEXT DEFAULT 'Want to Read', notes TEXT DEFAULT '', source TEXT DEFAULT '',
    current_page INTEGER DEFAULT 0, bookmark_page INTEGER DEFAULT 0,
    bookmark_note TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_books_title_author ON books(title, author);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

MIGRATIONS = {
    "current_page":  "ALTER TABLE books ADD COLUMN current_page INTEGER DEFAULT 0",
    "bookmark_page": "ALTER TABLE books ADD COLUMN bookmark_page INTEGER DEFAULT 0",
    "bookmark_note": "ALTER TABLE books ADD COLUMN bookmark_note TEXT DEFAULT ''",
    "buy_link":      "ALTER TABLE books ADD COLUMN buy_link TEXT DEFAULT ''",
    "description":   "ALTER TABLE books ADD COLUMN description TEXT DEFAULT ''",
}


# ── Connection ─────────────────────────────────────────────────────────────────

@contextmanager
def connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _get_columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()}


# ── Init ───────────────────────────────────────────────────────────────────────

def ensure_default_settings(conn) -> None:
    for key, value in DEFAULT_SCORE_SETTINGS.items():
        stored = json.dumps(value) if isinstance(value, (dict, list, int, float, bool)) else str(value)
        conn.execute(
            "INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)",
            (key, stored),
        )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(CREATE_SQL)
        existing = _get_columns(conn)
        for col, sql in MIGRATIONS.items():
            if col not in existing:
                conn.execute(sql)
        ensure_default_settings(conn)


# ── Settings ───────────────────────────────────────────────────────────────────

def get_settings(conn=None) -> dict[str, Any]:
    owns = conn is None
    if owns:
        ctx = connect()
        conn = ctx.__enter__()
    try:
        ensure_default_settings(conn)
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        data = dict(DEFAULT_SCORE_SETTINGS)
        for row in rows:
            try:
                data[row["key"]] = json.loads(row["value"])
            except Exception:
                data[row["key"]] = row["value"]
        return data
    finally:
        if owns:
            ctx.__exit__(None, None, None)


def update_settings(payload: dict, conn=None) -> dict[str, Any]:
    owns = conn is None
    if owns:
        ctx = connect()
        conn = ctx.__enter__()
    try:
        ensure_default_settings(conn)
        for key, value in payload.items():
            stored = json.dumps(value) if isinstance(value, (dict, list, int, float, bool)) else str(value)
            conn.execute(
                "INSERT INTO app_settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, stored),
            )
        return get_settings(conn)
    finally:
        if owns:
            ctx.__exit__(None, None, None)


# ── Backup / restore ───────────────────────────────────────────────────────────

def backup_db(reason: str = "manual") -> str | None:
    if not DB_PATH.exists():
        return None
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"library_{reason}_{ts}.db"
    shutil.copy2(DB_PATH, target)
    return str(target)


def list_backups() -> list[dict]:
    backup_dir = DB_PATH.parent / "backups"
    if not backup_dir.exists():
        return []
    items = []
    for path in sorted(backup_dir.glob("library_*.db"), reverse=True):
        stat = path.stat()
        items.append({
            "name": path.name, "path": str(path), "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return items


def restore_backup(name: str) -> dict:
    backup_dir = DB_PATH.parent / "backups"
    target = backup_dir / name
    if not target.exists():
        raise FileNotFoundError(name)
    pre = backup_db("pre_restore")
    shutil.copy2(target, DB_PATH)
    return {"restored_from": str(target), "pre_restore_backup": pre}


def delete_backup(name: str) -> dict:
    backup_dir = DB_PATH.parent / "backups"
    target = backup_dir / name
    if not target.exists():
        raise FileNotFoundError(name)
    target.unlink()
    return {"deleted": True, "name": name}
