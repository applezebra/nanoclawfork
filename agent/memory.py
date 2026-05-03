"""SQLite-backed per-chat conversation store (L3)."""
from __future__ import annotations

import os
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

    def history(self, chat_id: int, limit: int = 20) -> list[dict]:
        """Return the last *limit* turns for *chat_id* in chronological order.

        Queries newest-first (ORDER BY ts DESC LIMIT ?), then reverses in
        Python so the caller receives oldest-to-newest (natural reading order).
        Returns [] for an unknown chat_id; never raises.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM turns"
                " WHERE chat_id = ? ORDER BY ts DESC, rowid DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]


def default_db_path() -> Path:
    """Return the default SQLite path derived from AGENT_DATA_DIR env var.

    Falls back to '/data' when AGENT_DATA_DIR is unset.
    Always returns a Path object (not str) — eng-review T3.
    """
    agent_data_dir = os.environ.get("AGENT_DATA_DIR", "/data")
    return Path(agent_data_dir) / "agent.sqlite"
