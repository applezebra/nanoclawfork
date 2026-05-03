"""Tests for L3 Step 1 + Step 2: Memory schema, append, history, default path."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from agent.memory import Memory, default_db_path


class TestMemoryInit:
    def test_construction_succeeds(self, tmp_path) -> None:
        """Memory(path) constructs without raising."""
        mem = Memory(tmp_path / "agent.sqlite")
        assert mem is not None

    def test_db_file_exists_after_construction(self, tmp_path) -> None:
        """The SQLite file is created on disk when Memory is constructed."""
        db = tmp_path / "agent.sqlite"
        Memory(db)
        assert db.exists()

    def test_idempotent_init_two_instances_same_path(self, tmp_path) -> None:
        """Constructing two Memory instances on the same path does not error.

        Proves CREATE TABLE IF NOT EXISTS is idempotent across container restarts.
        """
        db = tmp_path / "agent.sqlite"
        Memory(db)
        Memory(db)  # must not raise

    def test_wal_mode_enabled(self, tmp_path) -> None:
        """WAL journal mode is set after construction."""
        db = tmp_path / "agent.sqlite"
        Memory(db)
        with sqlite3.connect(db) as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row is not None
        assert row[0] == "wal"


class TestMemoryAppend:
    def test_append_succeeds(self, tmp_path) -> None:
        """append() does not raise for a valid call."""
        mem = Memory(tmp_path / "agent.sqlite")
        mem.append(chat_id=1, role="user", content="hello")

    def test_append_row_visible_in_db(self, tmp_path) -> None:
        """Appended row is readable via raw sqlite3 query."""
        db = tmp_path / "agent.sqlite"
        mem = Memory(db)
        mem.append(chat_id=1, role="user", content="hello")
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT chat_id, role, content FROM turns WHERE chat_id = ?", (1,)
            ).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "user", "hello")


class TestMemoryLogScrubbing:
    """eng-review T1 (P2): verify chat_id and content never appear in log output."""

    def test_chat_id_and_content_not_logged(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """chat_id and content must NOT appear in any agent.memory log record.

        role and len MUST appear so we know the DEBUG line fired correctly.
        """
        secret_content = "this-is-private-content"
        mem = Memory(tmp_path / "agent.sqlite")
        with caplog.at_level(logging.DEBUG, logger="agent.memory"):
            mem.append(chat_id=42424242, role="user", content=secret_content)

        all_messages = " ".join(r.getMessage() for r in caplog.records)

        # PII / privacy: these must NOT appear
        assert "42424242" not in all_messages, (
            f"chat_id leaked into log: {all_messages!r}"
        )
        assert secret_content not in all_messages, (
            f"content leaked into log: {all_messages!r}"
        )

        # Operational info: these MUST appear (proves the DEBUG line fired)
        assert "role=user" in all_messages or "role" in all_messages, (
            f"Expected role in log output; got: {all_messages!r}"
        )
        expected_len = str(len(secret_content))  # compute dynamically — no off-by-one risk
        assert f"len={expected_len}" in all_messages, (
            f"Expected 'len={expected_len}' in log; got: {all_messages!r}"
        )


class TestSQLInjectionSafety:
    """eng-review T2 (P3): parameterized ? placeholders prevent SQL injection."""

    def test_injection_string_stored_as_literal(self, tmp_path) -> None:
        """Role field containing SQL injection payload is stored literally, not executed.

        Verifies: (a) row was inserted, (b) table still exists, (c) role round-trips.
        """
        db = tmp_path / "agent.sqlite"
        mem = Memory(db)
        injection_role = "'; DROP TABLE turns; --"

        mem.append(chat_id=1, role=injection_role, content="payload")

        # (a) Row was inserted and role round-trips as the literal string
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT role FROM turns WHERE chat_id = ?", (1,)
            ).fetchall()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0][0] == injection_role, (
            f"role did not round-trip: expected {injection_role!r}, got {rows[0][0]!r}"
        )

        # (b) Table still exists — injection did not execute DROP TABLE
        # Prove by appending a second row successfully
        mem.append(chat_id=1, role="user", content="second")
        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        assert count == 2, f"Expected 2 rows after second append, got {count}"


class TestMemoryHistory:
    """L3 Step 2: history() retrieval — ordering, limits, isolation, empty."""

    def test_history_returns_all_turns_chronological(self, tmp_path) -> None:
        """Append 5 turns → history(1) returns 5 in oldest-first order."""
        mem = Memory(tmp_path / "agent.sqlite")
        expected = [
            ("user", f"msg-{i}") for i in range(5)
        ]
        for role, content in expected:
            mem.append(chat_id=1, role=role, content=content)

        result = mem.history(chat_id=1)

        assert len(result) == 5
        for i, turn in enumerate(result):
            assert turn["role"] == expected[i][0]
            assert turn["content"] == expected[i][1]

    def test_history_limit_returns_most_recent(self, tmp_path) -> None:
        """Append 25 turns → history(2, limit=20) returns exactly 20, most recent, oldest-first."""
        mem = Memory(tmp_path / "agent.sqlite")
        for i in range(25):
            mem.append(chat_id=2, role="user", content=f"turn-{i}")

        result = mem.history(chat_id=2, limit=20)

        assert len(result) == 20
        # The 20 most recent are turns 5..24; oldest of that set is turn-5
        assert result[0]["content"] == "turn-5"
        assert result[-1]["content"] == "turn-24"

    def test_history_isolates_by_chat_id(self, tmp_path) -> None:
        """Turns from chat_id=2 must not appear in history(1)."""
        mem = Memory(tmp_path / "agent.sqlite")
        mem.append(chat_id=1, role="user", content="chat1-msg")
        mem.append(chat_id=2, role="user", content="chat2-msg")

        result = mem.history(chat_id=1)

        assert len(result) == 1
        assert result[0]["content"] == "chat1-msg"

    def test_history_empty_for_unknown_chat_id(self, tmp_path) -> None:
        """history(999) returns [] without raising when no turns exist."""
        mem = Memory(tmp_path / "agent.sqlite")
        result = mem.history(chat_id=999)
        assert result == []

    def test_history_persists_across_restart(self, tmp_path) -> None:
        """AC-5: dropping Memory and constructing a new one at the same path
        must not wipe existing data (CREATE TABLE IF NOT EXISTS is idempotent)."""
        db = tmp_path / "agent.sqlite"
        mem = Memory(db)
        mem.append(chat_id=1, role="user", content="persistent-a")
        mem.append(chat_id=1, role="assistant", content="persistent-b")
        mem.append(chat_id=1, role="user", content="persistent-c")
        del mem  # simulate container restart

        mem2 = Memory(db)
        result = mem2.history(chat_id=1)

        assert len(result) == 3
        assert result[0]["content"] == "persistent-a"
        assert result[1]["content"] == "persistent-b"
        assert result[2]["content"] == "persistent-c"


class TestDefaultDbPath:
    """L3 Step 2: default_db_path() returns correct Path based on env var."""

    def test_default_path_when_env_unset(self, monkeypatch) -> None:
        """With AGENT_DATA_DIR unset, returns Path('/data/agent.sqlite')."""
        monkeypatch.delenv("AGENT_DATA_DIR", raising=False)
        assert default_db_path() == Path("/data/agent.sqlite")

    def test_default_path_with_custom_env(self, monkeypatch) -> None:
        """With AGENT_DATA_DIR='/custom', returns Path('/custom/agent.sqlite')."""
        monkeypatch.setenv("AGENT_DATA_DIR", "/custom")
        assert default_db_path() == Path("/custom/agent.sqlite")

    def test_return_type_is_path_not_str(self, monkeypatch) -> None:
        """eng-review T3: default_db_path() must return Path, not str."""
        monkeypatch.delenv("AGENT_DATA_DIR", raising=False)
        assert isinstance(default_db_path(), Path)
