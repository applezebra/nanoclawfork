"""Persistent memory — SQLite, mounted as a volume from the host.

Stage 1: minimal — message history per chat_id.
Stage 2: vector recall, scheduled jobs table, episodic summaries.

The DB file lives at AGENT_DATA_DIR/agent.db, which is the only writable
mount inside the container. Everything else is read-only.
"""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    role        TEXT NOT NULL,           -- 'user' | 'assistant' | 'tool'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, created_at);
"""


def _db_path() -> Path:
    data_dir = Path(os.environ.get("AGENT_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "agent.db"


async def init_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def append(chat_id: str, role: str, content: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO messages(chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
        await db.commit()


async def recent(chat_id: str, limit: int = 20) -> list[tuple[str, str]]:
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
    return list(reversed([(r[0], r[1]) for r in rows]))
