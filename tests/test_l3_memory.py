"""Tests for L3 Step 1: Memory schema initialization and append."""
from __future__ import annotations

import logging
import sqlite3

import pytest

from agent.memory import Memory


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
