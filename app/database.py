from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY,
                image_name TEXT NOT NULL,
                image_mime TEXT NOT NULL,
                image_hash TEXT NOT NULL UNIQUE,
                subject TEXT,
                recognized_text TEXT NOT NULL DEFAULT '',
                hint_level INTEGER NOT NULL DEFAULT 0,
                reveal_answer INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings(key, value) VALUES ('learning_mode', '1');
            """
        )


def question_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def messages_for(question_id: str) -> list[dict]:
    with connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE question_id=? ORDER BY id",
            (question_id,),
        )]


def add_message(question_id: str, role: str, content: str) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO chat_messages(question_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (question_id, role, content, now_iso()),
        )
