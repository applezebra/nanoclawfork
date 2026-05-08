"""Tests for L2: Turn TypedDict shape, _frame_user_text framing, and reply() function."""
from __future__ import annotations

import logging
import re

import pytest

from agent.config import ConfigError
from agent.registry import ResolvedProvider
from agent.runtime import Turn, _frame_user_text, reply


class TestTurnShape:
    def test_instantiate_user_turn(self) -> None:
        turn: Turn = {"role": "user", "content": "hello"}
        assert turn["role"] == "user"
        assert turn["content"] == "hello"

    def test_instantiate_assistant_turn(self) -> None:
        turn: Turn = {"role": "assistant", "content": "world"}
        assert turn["role"] == "assistant"
        assert turn["content"] == "world"


class TestFrameUserText:
    def test_happy_path_envelope_shape(self) -> None:
        result = _frame_user_text("hello world", chat_id=12345)
        assert result.startswith('<user_message chat_id="12345">')
        assert "hello world" in result
        assert result.endswith("</user_message>")

    def test_escape_closing_tag_in_body(self) -> None:
        result = _frame_user_text("escape </user_message> me", chat_id=1)
        # The body must NOT contain a bare </user_message> that could break the envelope.
        # Strip the legitimate outer closing tag before checking.
        outer_close = "</user_message>"
        # Remove the trailing outer closing tag to inspect the body only.
        body_and_open = result[: result.rfind(outer_close)]
        assert outer_close not in body_and_open

    def test_escape_uses_backslash_form(self) -> None:
        result = _frame_user_text("</user_message>", chat_id=1)
        assert r"<\/user_message>" in result

    def test_empty_text_returns_valid_envelope(self) -> None:
        result = _frame_user_text("", chat_id=0)
        assert result.startswith('<user_message chat_id="0">')
        assert result.endswith("</user_message>")
        # Body between opening and closing tags should be whitespace / empty only.
        open_tag = '<user_message chat_id="0">'
        close_tag = "</user_message>"
        body = result[len(open_tag) : result.rfind(close_tag)]
        assert body.strip() == ""

    def test_chat_id_must_be_int_rejects_string(self) -> None:
        """codex-review P1: type hint alone is not the defense; isinstance check enforces it."""
        with pytest.raises(TypeError, match="chat_id must be int"):
            _frame_user_text("hello", chat_id='1" bad="injected')  # type: ignore[arg-type]

    def test_chat_id_must_be_int_rejects_bool(self) -> None:
        """bool is a subclass of int in Python; reject it explicitly to prevent silent misuse."""
        with pytest.raises(TypeError, match="chat_id must be int"):
            _frame_user_text("hello", chat_id=True)  # type: ignore[arg-type]

    def test_chat_id_must_be_int_rejects_none(self) -> None:
        with pytest.raises(TypeError, match="chat_id must be int"):
            _frame_user_text("hello", chat_id=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers for Step 2 tests
# ---------------------------------------------------------------------------

def _make_provider(kind: str = "openai_compatible") -> ResolvedProvider:
    """Build a minimal ResolvedProvider for testing."""
    return ResolvedProvider(
        provider_name="test-provider",
        kind=kind,
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model_id="test-model",
    )


def _make_fn_model(captured: list, reply_text: str = "ok"):
    """Return a FunctionModel that captures messages and returns reply_text."""
    from pydantic_ai.models.function import FunctionModel, AgentInfo
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(list(messages))
        return ModelResponse(parts=[TextPart(content=reply_text)])

    return FunctionModel(fn)


def _patch_agent(monkeypatch: pytest.MonkeyPatch, fn_model) -> None:
    """Monkeypatch agent.runtime.Agent so reply() uses fn_model instead of the real model."""
    import agent.runtime as rt
    original = rt.Agent

    class PatchedAgent(original):  # type: ignore[misc, valid-type]
        def __init__(self, model, **kwargs):  # type: ignore[override]
            super().__init__(fn_model, **kwargs)

    monkeypatch.setattr(rt, "Agent", PatchedAgent)


# ---------------------------------------------------------------------------
# Step 2: reply() function tests
# ---------------------------------------------------------------------------

class TestReply:
    """Tests for the async reply() function."""

    @pytest.mark.anyio
    async def test_happy_path_returns_model_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FunctionModel returning 'ok' → reply() returns 'ok'."""
        captured: list = []
        _patch_agent(monkeypatch, _make_fn_model(captured, "ok"))
        result = await reply(_make_provider(), "sys", [], "hello", chat_id=1)
        assert result == "ok"

    @pytest.mark.anyio
    async def test_framing_reaches_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The framed envelope is what the model sees as the user prompt."""
        from pydantic_ai.messages import UserPromptPart

        captured: list = []
        _patch_agent(monkeypatch, _make_fn_model(captured))
        await reply(_make_provider(), "sys", [], "hello", chat_id=1)

        user_texts = []
        for msg in captured[0]:
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    c = part.content
                    user_texts.append(c if isinstance(c, str) else str(c))

        expected = '<user_message chat_id="1">\nhello\n</user_message>'
        assert any(expected in t for t in user_texts), (
            f"Expected framed envelope in user parts; got: {user_texts}"
        )

    @pytest.mark.anyio
    async def test_system_prompt_reaches_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T1: system_prompt is present as a SystemPromptPart in the captured messages."""
        from pydantic_ai.messages import SystemPromptPart

        captured: list = []
        _patch_agent(monkeypatch, _make_fn_model(captured))
        await reply(_make_provider(), "my system prompt", [], "hello", chat_id=1)

        system_contents = []
        for msg in captured[0]:
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    system_contents.append(part.content)

        assert any("my system prompt" in c for c in system_contents), (
            f"system_prompt not found; system parts: {system_contents}"
        )

    @pytest.mark.anyio
    async def test_history_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T2: prior history turns appear in order before the current framed user text."""
        from pydantic_ai.messages import UserPromptPart, TextPart

        captured: list = []
        _patch_agent(monkeypatch, _make_fn_model(captured))
        history: list[Turn] = [
            {"role": "user", "content": "prior"},
            {"role": "assistant", "content": "prior reply"},
        ]
        await reply(_make_provider(), "sys", history, "current", chat_id=2)

        all_text: list[str] = []
        for msg in captured[0]:
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    c = part.content
                    all_text.append(c if isinstance(c, str) else str(c))
                elif isinstance(part, TextPart):
                    all_text.append(part.content)

        assert "prior" in all_text, f"'prior' not found in {all_text}"
        assert "prior reply" in all_text, f"'prior reply' not found in {all_text}"
        prior_idx = next(i for i, t in enumerate(all_text) if t == "prior")
        reply_idx = next(i for i, t in enumerate(all_text) if t == "prior reply")
        current_idx = next(i for i, t in enumerate(all_text) if "current" in t)
        assert prior_idx < reply_idx < current_idx, (
            f"Order wrong: prior@{prior_idx}, prior_reply@{reply_idx}, current@{current_idx}"
        )

    @pytest.mark.anyio
    async def test_empty_history_first_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty history: reply returns OK; only one user-prompt part (the framed message)."""
        from pydantic_ai.messages import UserPromptPart

        captured: list = []
        _patch_agent(monkeypatch, _make_fn_model(captured, "first reply"))
        result = await reply(_make_provider(), "sys", [], "hi", chat_id=5)
        assert result == "first reply"

        user_parts = []
        for msg in captured[0]:
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    c = part.content
                    user_parts.append(c if isinstance(c, str) else str(c))

        assert len(user_parts) == 1, f"Expected 1 user part, got {len(user_parts)}: {user_parts}"
        assert '<user_message chat_id="5">' in user_parts[0]

    @pytest.mark.anyio
    async def test_unknown_kind_raises_config_error(self) -> None:
        """provider.kind not in {'openai_compatible'} → ConfigError (defensive guard)."""
        bad_provider = ResolvedProvider(
            provider_name="ollama",
            kind="ollama",
            base_url="http://localhost:11434",
            api_key=None,
            model_id="llama3",
        )
        with pytest.raises(ConfigError, match="Unknown provider kind"):
            await reply(bad_provider, "sys", [], "hello", chat_id=1)

    @pytest.mark.anyio
    async def test_exception_propagates_and_key_not_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exception propagates; api_key value is NOT visible in any log output path.

        Defense-in-depth verification (codex-review L2 P2): we assert against
        BOTH caplog (the record-mutation path) AND a real StreamHandler with a
        Formatter (the actual output path). A regression in the Formatter patch
        would still pass the caplog check, so the StreamHandler check is the one
        that actually proves the protection holds.
        """
        import io
        from pydantic_ai.models.function import FunctionModel, AgentInfo
        from pydantic_ai.messages import ModelMessage, ModelResponse
        import agent.runtime as rt
        import agent.logging as alog

        secret = "test-key-value-12345"
        monkeypatch.setenv("DEEPINFRA_API_KEY", secret)

        # Ensure the scrubber knows this secret (it was collected at module import;
        # add it manually so the test is not sensitive to import order).
        if secret not in alog._SECRETS:
            alog._SECRETS.insert(0, secret)

        def raising_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError(f"simulated error containing DEEPINFRA_API_KEY={secret}")

        fn_model = FunctionModel(raising_fn)
        original = rt.Agent

        class PatchedAgent(original):  # type: ignore[misc, valid-type]
            def __init__(self, model, **kwargs):  # type: ignore[override]
                super().__init__(fn_model, **kwargs)

        monkeypatch.setattr(rt, "Agent", PatchedAgent)

        provider = ResolvedProvider(
            provider_name="deepinfra",
            kind="openai_compatible",
            base_url="https://api.deepinfra.com/v1/openai",
            api_key=secret,
            model_id="meta-llama/Llama-3.3-70B-Instruct",
        )

        # Attach a real StreamHandler with a Formatter so we observe the actual
        # emitted output, not just the in-memory record.
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s\n%(exc_text)s"))
        handler.setLevel(logging.ERROR)
        runtime_logger = logging.getLogger("agent.runtime")
        runtime_logger.addHandler(handler)
        try:
            with caplog.at_level(logging.ERROR, logger="agent.runtime"):
                with pytest.raises(RuntimeError):
                    await reply(provider, "sys", [], "hello", chat_id=1)
        finally:
            runtime_logger.removeHandler(handler)

        # 1. Real handler output (the path that matters for production).
        emitted = buf.getvalue()
        assert secret not in emitted, (
            f"Secret leaked through real StreamHandler output: {emitted!r}"
        )
        assert "[REDACTED]" in emitted, (
            f"Expected [REDACTED] in emitted output: {emitted!r}"
        )

        # 2. caplog records (defense-in-depth — record-level scrub backstop).
        for record in caplog.records:
            assert secret not in record.getMessage(), (
                f"Secret found in record.getMessage(): {record.getMessage()!r}"
            )
            if record.exc_text:
                assert secret not in record.exc_text, (
                    f"Secret found in record.exc_text: {record.exc_text!r}"
                )

    def test_no_anthropic_import_in_runtime(self) -> None:
        """A2: neither 'import anthropic' nor 'from anthropic' appears in agent/runtime.py."""
        import pathlib
        source = pathlib.Path(__file__).parent.parent / "agent" / "runtime.py"
        text = source.read_text()
        match = re.search(r"^\s*(import anthropic|from anthropic)", text, re.M)
        assert match is None, (
            f"Found Anthropic import in agent/runtime.py: {match.group()!r}"
        )


# ---------------------------------------------------------------------------
# Step 3 (v0.1.4) — reply_with_fallback() tests
# ---------------------------------------------------------------------------

def _named_provider(provider_name: str, model_id: str = "m") -> ResolvedProvider:
    return ResolvedProvider(
        provider_name=provider_name,
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        api_key="k",
        model_id=model_id,
    )


class _Counter:
    def __init__(self) -> None:
        self.calls: list[str] = []


def _make_reply_stub(behaviors: dict[str, object], counter: _Counter):
    """behaviors: provider_name -> reply text (str) or Exception instance/class to raise."""

    async def fake_reply(provider, system_prompt, history, user_text, chat_id):
        counter.calls.append(provider.provider_name)
        outcome = behaviors[provider.provider_name]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            raise outcome("simulated failure")
        return outcome

    return fake_reply


class TestReplyWithFallback:
    @pytest.mark.anyio
    async def test_t1_primary_succeeds_no_fallback_consulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent import runtime as rt

        counter = _Counter()
        monkeypatch.setattr(
            rt, "reply", _make_reply_stub({"primary": "ok"}, counter)
        )
        primary = _named_provider("primary")
        fallbacks = [_named_provider("fb1"), _named_provider("fb2")]
        result = await rt.reply_with_fallback(primary, fallbacks, "sys", [], "hi", chat_id=1)
        assert result == "ok"
        assert counter.calls == ["primary"]

    @pytest.mark.anyio
    async def test_t2_primary_fails_first_fallback_returns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent import runtime as rt

        counter = _Counter()
        monkeypatch.setattr(
            rt,
            "reply",
            _make_reply_stub(
                {"primary": RuntimeError("boom"), "fb1": "fb1-reply", "fb2": "unused"},
                counter,
            ),
        )
        primary = _named_provider("primary")
        fallbacks = [_named_provider("fb1"), _named_provider("fb2")]
        result = await rt.reply_with_fallback(primary, fallbacks, "sys", [], "hi", chat_id=1)
        assert result == "fb1-reply"
        assert counter.calls == ["primary", "fb1"]

    @pytest.mark.anyio
    async def test_t3_primary_and_first_fallback_fail_second_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent import runtime as rt

        counter = _Counter()
        monkeypatch.setattr(
            rt,
            "reply",
            _make_reply_stub(
                {
                    "primary": RuntimeError("p"),
                    "fb1": ValueError("v"),
                    "fb2": "fb2-reply",
                },
                counter,
            ),
        )
        primary = _named_provider("primary")
        fallbacks = [_named_provider("fb1"), _named_provider("fb2")]
        result = await rt.reply_with_fallback(primary, fallbacks, "sys", [], "hi", chat_id=1)
        assert result == "fb2-reply"
        assert counter.calls == ["primary", "fb1", "fb2"]

    @pytest.mark.anyio
    async def test_t4_all_fail_raises_all_providers_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from agent import runtime as rt

        counter = _Counter()
        monkeypatch.setattr(
            rt,
            "reply",
            _make_reply_stub(
                {
                    "primary": RuntimeError("p"),
                    "fb1": ValueError("v"),
                    "fb2": KeyError("k"),
                },
                counter,
            ),
        )
        primary = _named_provider("primary", "m1")
        fallbacks = [_named_provider("fb1", "m2"), _named_provider("fb2", "m3")]

        with caplog.at_level(logging.CRITICAL, logger="agent.runtime"):
            with pytest.raises(rt.AllProvidersFailed) as exc_info:
                await rt.reply_with_fallback(primary, fallbacks, "sys", [], "hi", chat_id=1)

        assert counter.calls == ["primary", "fb1", "fb2"]
        attempts = exc_info.value.attempts
        assert attempts == [
            ("primary", "m1", "RuntimeError"),
            ("fb1", "m2", "ValueError"),
            ("fb2", "m3", "KeyError"),
        ]
        critical_msgs = [
            r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL
        ]
        assert any("primary/m1: RuntimeError" in m for m in critical_msgs)
        assert any("fb2/m3: KeyError" in m for m in critical_msgs)

    @pytest.mark.anyio
    async def test_t5_logs_do_not_leak_exception_str(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from agent import runtime as rt

        counter = _Counter()
        secret_blob = "SUPER-SECRET-REQUEST-BODY-12345"
        monkeypatch.setattr(
            rt,
            "reply",
            _make_reply_stub(
                {
                    "primary": RuntimeError(secret_blob),
                    "fb1": ValueError(secret_blob),
                },
                counter,
            ),
        )
        primary = _named_provider("primary")
        fallbacks = [_named_provider("fb1")]

        with caplog.at_level(logging.INFO, logger="agent.runtime"):
            with pytest.raises(rt.AllProvidersFailed):
                await rt.reply_with_fallback(primary, fallbacks, "sys", [], "hi", chat_id=1)

        for record in caplog.records:
            # Skip the underlying reply()'s exc_info traceback (it's the SDK
            # path's responsibility, scrubbed by the L0 logger). We assert
            # the wrapper's own log lines (INFO fallback + CRITICAL summary)
            # contain only class names, never str(exc).
            if record.name != "agent.runtime":
                continue
            if record.exc_info:
                continue
            assert secret_blob not in record.getMessage(), (
                f"Exception str leaked into log: {record.getMessage()!r}"
            )

    @pytest.mark.anyio
    async def test_t6_empty_fallback_passes_through_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-1: empty fallback list → reply()'s exception bubbles, no AllProvidersFailed."""
        from agent import runtime as rt

        counter = _Counter()
        monkeypatch.setattr(
            rt,
            "reply",
            _make_reply_stub({"primary": RuntimeError("boom")}, counter),
        )
        primary = _named_provider("primary")
        with pytest.raises(RuntimeError, match="boom"):
            await rt.reply_with_fallback(primary, [], "sys", [], "hi", chat_id=1)
        assert counter.calls == ["primary"]

    @pytest.mark.anyio
    async def test_t6b_empty_fallback_happy_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-1: empty fallback list + primary success → returns primary's reply."""
        from agent import runtime as rt

        counter = _Counter()
        monkeypatch.setattr(
            rt, "reply", _make_reply_stub({"primary": "ok"}, counter)
        )
        primary = _named_provider("primary")
        result = await rt.reply_with_fallback(primary, [], "sys", [], "hi", chat_id=1)
        assert result == "ok"
        assert counter.calls == ["primary"]

    def test_t7_all_providers_failed_attempts_shape(self) -> None:
        from agent.runtime import AllProvidersFailed

        attempts = [
            ("openrouter", "model-a", "RuntimeError"),
            ("groq", "model-b", "TimeoutError"),
        ]
        exc = AllProvidersFailed(attempts)
        assert exc.attempts == attempts
        msg = str(exc)
        assert "2 provider(s) failed" in msg
        assert "openrouter/model-a: RuntimeError" in msg
        assert "groq/model-b: TimeoutError" in msg
