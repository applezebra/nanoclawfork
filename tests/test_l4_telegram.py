"""Tests for L4 Steps 1-3: _load_env, _is_allowed, _make_handler, run."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.config import ConfigError
from agent.connectors import telegram as tg
from agent.connectors.telegram import _is_allowed, _load_env, _make_handler
from agent.registry import ResolvedProvider


def _make_resolved(name: str = "deepinfra", model: str = "meta-llama/L") -> ResolvedProvider:
    return ResolvedProvider(
        provider_name=name,
        kind="openai_compatible",
        base_url="https://api.example/v1",
        api_key="fake-key",
        model_id=model,
    )


def _make_update(chat_id: int, text: str | None) -> MagicMock:
    """Synthetic python-telegram-bot Update object — only the fields the
    handler actually reads. effective_message.reply_text is an AsyncMock so
    `await message.reply_text(...)` resolves cleanly under pytest-anyio."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message.text = text
    update.effective_message.reply_text = AsyncMock()
    return update


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


class TestIsAllowed:
    """L4 Step 2: pure allowlist filter — no logging, no exceptions."""

    def test_allowed_id_returns_true(self) -> None:
        assert _is_allowed(12345, {12345, 67890}) is True

    def test_unknown_id_returns_false(self) -> None:
        assert _is_allowed(99999, {12345, 67890}) is False

    def test_empty_allowlist_blocks_everyone(self) -> None:
        """Defensive: even though _load_env refuses to construct an empty
        allowlist, _is_allowed must still deny when handed one."""
        assert _is_allowed(12345, set()) is False

    def test_negative_supergroup_id_allowed(self) -> None:
        """Telegram supergroup IDs are negative; round-trip cleanly."""
        assert _is_allowed(-1001234567890, {-1001234567890}) is True

    def test_no_logging_on_call(self, caplog: pytest.LogCaptureFixture) -> None:
        """_is_allowed is pure: caller is responsible for auth-drop logging."""
        import logging
        with caplog.at_level(logging.DEBUG, logger="agent.connectors.telegram"):
            _is_allowed(12345, {12345})
            _is_allowed(99999, {12345})
        assert caplog.records == []


class TestHandlerHappyPath:
    """L4 Step 3: end-to-end handler with mocked memory + runtime."""

    pytestmark = pytest.mark.anyio

    async def test_authorized_message_full_flow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = MagicMock()
        memory.history.return_value = []
        reply_mock = AsyncMock(return_value="hi back")
        monkeypatch.setattr(tg.runtime, "reply", reply_mock)

        resolved = _make_resolved()
        handler = _make_handler({42}, resolved, "you are helpful", memory)

        update = _make_update(chat_id=42, text="hello")
        await handler(update, MagicMock())

        # Two appends: user, then assistant — in order
        assert memory.append.call_count == 2
        assert memory.append.call_args_list[0].args == (42, "user", "hello")
        assert memory.append.call_args_list[1].args == (42, "assistant", "hi back")

        reply_mock.assert_awaited_once_with(resolved, "you are helpful", [], "hello", 42)
        update.effective_message.reply_text.assert_awaited_once_with("hi back")


class TestHandlerAuthDrop:
    """eng-review T1: unauthorized chat_id → no memory write, no LLM call, no reply."""

    pytestmark = pytest.mark.anyio

    async def test_unauthorized_chat_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = MagicMock()
        reply_mock = AsyncMock()
        monkeypatch.setattr(tg.runtime, "reply", reply_mock)

        handler = _make_handler({42}, _make_resolved(), "sp", memory)
        update = _make_update(chat_id=99, text="probe")
        await handler(update, MagicMock())

        memory.append.assert_not_called()
        memory.history.assert_not_called()
        reply_mock.assert_not_awaited()
        update.effective_message.reply_text.assert_not_awaited()


class TestHandlerResilience:
    """eng-review T2: handler must not crash the polling loop on internal failure."""

    pytestmark = pytest.mark.anyio

    async def test_memory_failure_does_not_propagate(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import sqlite3
        memory = MagicMock()
        memory.append.side_effect = sqlite3.OperationalError("disk I/O error")
        reply_mock = AsyncMock()
        monkeypatch.setattr(tg.runtime, "reply", reply_mock)

        handler = _make_handler({42}, _make_resolved(), "sp", memory)
        update = _make_update(chat_id=42, text="hello")

        with caplog.at_level(logging.ERROR, logger="agent.connectors.telegram"):
            await handler(update, MagicMock())  # must not raise

        # We never got past the failed append → no LLM call
        reply_mock.assert_not_awaited()
        # And the failure was logged at ERROR
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    async def test_handler_recovers_for_subsequent_messages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Polling-loop survival: a fresh handler call after a failure works."""
        call = {"n": 0}

        def append_first_fails(*_a, **_kw) -> None:
            call["n"] += 1
            if call["n"] == 1:
                import sqlite3
                raise sqlite3.OperationalError("flaky disk")

        memory = MagicMock()
        memory.append.side_effect = append_first_fails
        memory.history.return_value = []
        reply_mock = AsyncMock(return_value="ok")
        monkeypatch.setattr(tg.runtime, "reply", reply_mock)

        handler = _make_handler({42}, _make_resolved(), "sp", memory)

        # First call: append raises → handler swallows
        await handler(_make_update(42, "first"), MagicMock())
        # Second call: append succeeds → full flow
        await handler(_make_update(42, "second"), MagicMock())

        # 1 (failed) + 2 (user + assistant from second flow) = 3 invocations
        assert memory.append.call_count == 3
        reply_mock.assert_awaited_once()


class TestHandlerLogHygiene:
    """eng-review T3: reply text never appears in logs; only metadata."""

    pytestmark = pytest.mark.anyio

    async def test_reply_text_not_in_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        sentinel = "sensitive-reply-content-XYZ-789"
        memory = MagicMock()
        memory.history.return_value = []
        monkeypatch.setattr(tg.runtime, "reply", AsyncMock(return_value=sentinel))

        handler = _make_handler({42}, _make_resolved(), "sp", memory)

        with caplog.at_level(logging.DEBUG, logger="agent.connectors.telegram"):
            await handler(_make_update(42, "ask"), MagicMock())

        all_msgs = " ".join(r.getMessage() for r in caplog.records)
        assert sentinel not in all_msgs, f"reply text leaked into log: {all_msgs!r}"
        # And the metadata-only line DID fire — len matches the sentinel.
        assert f"reply-sent: chat_id=42 reply_len={len(sentinel)}" in all_msgs

    async def test_user_text_not_in_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """User-supplied text must not be logged either (privacy parity with L3)."""
        secret_user_text = "my-private-question-ABCDEF"
        memory = MagicMock()
        memory.history.return_value = []
        monkeypatch.setattr(tg.runtime, "reply", AsyncMock(return_value="r"))

        handler = _make_handler({42}, _make_resolved(), "sp", memory)
        with caplog.at_level(logging.DEBUG, logger="agent.connectors.telegram"):
            await handler(_make_update(42, secret_user_text), MagicMock())

        all_msgs = " ".join(r.getMessage() for r in caplog.records)
        assert secret_user_text not in all_msgs


class TestHandlerTokenScrubbing:
    """eng-review-aligned: token must never appear in handler error logs.

    Cross-lane integration with L0's secret-scrubbing logger.
    """

    pytestmark = pytest.mark.anyio

    async def test_token_redacted_from_error_traceback(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from agent import logging as alog

        token = "FAKE_TG_TOKEN_FOR_SCRUB_TEST_5559"
        # The L0 scrubber reads secrets from env at import time; for a test
        # we register at runtime by appending to the private _SECRETS list,
        # then remove in finally.
        alog._SECRETS.append(token)
        try:
            memory = MagicMock()
            memory.history.return_value = []
            err_url = f"https://api.telegram.org/bot{token}/getUpdates failed"
            monkeypatch.setattr(
                tg.runtime, "reply", AsyncMock(side_effect=RuntimeError(err_url))
            )

            handler = _make_handler({42}, _make_resolved(), "sp", memory)
            with caplog.at_level(logging.ERROR, logger="agent.connectors.telegram"):
                await handler(_make_update(42, "ping"), MagicMock())

            all_msgs = " ".join(
                (r.getMessage() + " " + (r.exc_text or ""))
                for r in caplog.records
            )
            assert token not in all_msgs, (
                f"token leaked in error log: {all_msgs!r}"
            )
        finally:
            if token in alog._SECRETS:
                alog._SECRETS.remove(token)


class TestRunStartupLogging:
    """run() startup line emits provider/model + allowlist size only — no token."""

    def test_startup_log_omits_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from agent.config import AgentGroupSpec, Config, ProviderSpec
        from agent import logging as alog

        token = "STARTUP_LOG_SCRUB_TOKEN_8888"
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "42,43")

        cfg = Config(
            providers={
                "deepinfra": ProviderSpec(
                    kind="openai_compatible",
                    base_url="https://api.example/v1",
                    api_key_env="X_KEY",
                    allowed_models=["m1"],
                )
            },
            agents={
                "personal-assistant": AgentGroupSpec(
                    model="deepinfra/m1",
                    system_prompt="be helpful",
                ),
            },
        )

        # Stub registry.resolve so we don't touch real env keys
        def fake_resolver(_cfg, model_ref):
            assert model_ref == "deepinfra/m1"
            return _make_resolved()

        # Replace ApplicationBuilder so .build().run_polling() is a no-op.
        # We only care about the startup log line BEFORE app build is invoked
        # against the live network — mocking out run_polling stops the loop.
        fake_app = MagicMock()
        fake_builder = MagicMock()
        fake_builder.token.return_value = fake_builder
        fake_builder.build.return_value = fake_app
        monkeypatch.setattr(tg, "ApplicationBuilder", lambda: fake_builder)

        alog._SECRETS.append(token)
        try:
            with caplog.at_level(logging.INFO, logger="agent.connectors.telegram"):
                tg.run(cfg, fake_resolver, MagicMock())

            all_msgs = " ".join(r.getMessage() for r in caplog.records)
            assert token not in all_msgs, f"token leaked in startup log: {all_msgs!r}"
            # And the expected metadata DID appear
            assert "Connector starting:" in all_msgs
            assert "provider=deepinfra" in all_msgs
            assert "model=meta-llama/L" in all_msgs
            assert "allowlist_size=2" in all_msgs

            # And run_polling was actually called — proves we got to the end of run()
            fake_app.run_polling.assert_called_once()
            # The token was passed to .token() but never logged
            fake_builder.token.assert_called_once_with(token)
        finally:
            if token in alog._SECRETS:
                alog._SECRETS.remove(token)

    def test_run_raises_when_agent_group_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent.config import Config, ProviderSpec

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("ALLOWED_TELEGRAM_CHAT_IDS", "42")

        cfg = Config(
            providers={
                "p": ProviderSpec(
                    kind="openai_compatible",
                    base_url="https://x",
                    allowed_models=["*"],
                )
            },
            agents={},  # personal-assistant absent
        )

        with pytest.raises(ConfigError, match="personal-assistant"):
            tg.run(cfg, lambda c, r: _make_resolved(), MagicMock())
