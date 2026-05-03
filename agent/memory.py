"""SQLite-backed per-chat conversation store (L3)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from agent.logging import get_logger

_log = get_logger("agent.memory")


class Memory:
    """Persist conversation turns to a SQLite file; one row per turn."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init_schema()
        _log.info("Memory initialized at %s", db_path)

    def _init_schema(self) -> None:
        """Create turns table and enable WAL mode in one connection."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns"
                "(chat_id INTEGER, ts INTEGER, role TEXT, content TEXT)"
            )
            conn.execute("PRAGMA journal_mode=WAL")

    def append(self, chat_id: int, role: str, content: str) -> None:
        """Insert one turn row; never logs chat_id or content (privacy)."""
        ts = int(time.time())
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO turns(chat_id, ts, role, content) VALUES (?, ?, ?, ?)",
                (chat_id, ts, role, content),
            )
        _log.debug("append: role=%s len=%d", role, len(content))
