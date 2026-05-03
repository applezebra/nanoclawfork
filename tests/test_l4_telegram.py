"""Tests for L4 Step 1: _load_env() env-var validation."""
from __future__ import annotations

import logging

import pytest

from agent.config import ConfigError
from agent.connectors.telegram import _load_env


class TestLoadEnvHappyPath:
    def test_returns_token_and_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: valid token + two comma-separated IDs → correct tuple."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "12345,67890")
        token, allowlist = _load_env()
        assert token == "fake-token"
        assert allowlist == {12345, 67890}

    def test_whitespace_trimmed_in_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Extra spaces around IDs are stripped cleanly."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", " 111 , 222 ")
        _, allowlist = _load_env()
        assert allowlist == {111, 222}


class TestLoadEnvTokenValidation:
    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absent TELEGRAM_BOT_TOKEN → ConfigError naming the var."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "12345")
        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
            _load_env()

    def test_empty_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty string TELEGRAM_BOT_TOKEN → ConfigError naming the var."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "12345")
        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
            _load_env()

    def test_whitespace_only_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whitespace-only TELEGRAM_BOT_TOKEN → ConfigError naming the var."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "12345")
        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
            _load_env()


class TestLoadEnvAllowlistValidation:
    def test_missing_allowlist_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Absent ALLOWED_TELEGRAM_CHAT_IDS → ConfigError naming the var."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.delenv("ALLOWED_TELEGRAM_CHAT_IDS", raising=False)
        with pytest.raises(ConfigError, match="ALLOWED_TELEGRAM_CHAT_IDS"):
            _load_env()

    def test_empty_allowlist_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty string ALLOWED_TELEGRAM_CHAT_IDS → ConfigError naming the var."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "")
        with pytest.raises(ConfigError, match="ALLOWED_TELEGRAM_CHAT_IDS"):
            _load_env()

    def test_invalid_integer_raises_with_segment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-integer segment → ConfigError naming the offending segment."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "12345,notanint,67890")
        with pytest.raises(ConfigError, match="notanint"):
            _load_env()

    def test_all_blank_segments_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """eng-review T4: env present but all segments blank → ConfigError, not silent deny-all."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", ",, , ,")
        with pytest.raises(ConfigError, match="ALLOWED_TELEGRAM_CHAT_IDS"):
            _load_env()


class TestLoadEnvTokenNotInLogs:
    def test_token_not_in_log_output(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The literal token value must never appear in any captured log record.

        codex-review L4-Step1 P2: do NOT register the test token with the L0
        scrubber. If we did, a regression where _load_env() logs the token in
        plaintext would be silently scrubbed to [REDACTED] and the test would
        pass — defeating the purpose. Without the scrubber knowing the token,
        any direct logging of it appears as plaintext and the assertion fails
        loudly. Belt-and-suspenders: also assert the only emitted message is
        the expected count-only INFO line, so silent code paths that log
        anything beyond the count-line spec are flagged.
        """
        test_token = "FAKE_BOT_TOKEN_VALUE_FOR_LOG_TEST"
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", test_token)
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "42")

        with caplog.at_level(logging.INFO, logger="agent.connectors.telegram"):
            _load_env()

        # Token must not appear anywhere — the scrubber is intentionally
        # NOT registered with this token so a regression would surface.
        for record in caplog.records:
            assert test_token not in record.getMessage(), (
                f"Token leaked in log record: {record.getMessage()!r}"
            )

        # Belt-and-suspenders: exactly one record, and its message is exactly
        # the documented count-line. A regression that appends the allowlist
        # itself (e.g. "Allowlist loaded: 1 chat ID(s); allowlist={42}") would
        # leak the chat ID; this exact-match assertion hard-stops that
        # (codex-review L4-Step1 P2 follow-up).
        assert len(caplog.records) == 1, (
            f"Expected exactly 1 log record (the count line); got {len(caplog.records)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        emitted = caplog.records[0].getMessage()
        expected = "Allowlist loaded: 1 chat ID(s)"
        assert emitted == expected, (
            f"Expected exact message {expected!r}; got {emitted!r}. "
            f"A non-exact match implies _load_env() is logging more than the count "
            f"(e.g. the chat IDs themselves) — that's a leak."
        )


class TestLoadEnvAllowlistEdgeCases:
    """codex-review L4-Step1 P3: parametrized edge cases lock in parser behavior.

    Telegram chat IDs in the wild include negative supergroup IDs and very
    large positive values. Leading-zero strings and unicode whitespace are
    plausible config-file slip cases. These tests document the intended
    behavior so future tweaks to the parsing loop don't silently change it.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("-1001234567890", {-1001234567890}),  # Telegram supergroup ID
            ("12345,-1001234567890", {12345, -1001234567890}),  # mixed DM + group
            ("99999999999999999", {99999999999999999}),  # 17-digit ID (Python int handles arbitrary precision)
            ("00012345", {12345}),  # leading zeros — int() strips
            ("\t12345 ,\n67890\t", {12345, 67890}),  # standard whitespace stripped
        ],
    )
    def test_parses_edge_case_ids(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: set[int]
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", raw)
        _, allowlist = _load_env()
        assert allowlist == expected
