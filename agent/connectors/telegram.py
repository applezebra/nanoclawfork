"""Telegram polling connector — Steps 1-3: env, allowlist, polling loop."""
from __future__ import annotations

import os
from typing import Callable

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters

from agent import runtime
from agent.config import Config, ConfigError
from agent.logging import get_logger, register_secret
from agent.memory import Memory
from agent.registry import ResolvedProvider

_log = get_logger("agent.connectors.telegram")

# The agent group whose model + system_prompt drives the Telegram bot in 0.1.
# A future lane may make this configurable per-connector; for now there is
# exactly one connector and one group, so the constant lives here.
_AGENT_GROUP = "personal-assistant"

# Telegram's sendMessage hard limit. Text longer than this is rejected by the
# Bot API. We truncate before send AND before persisting the assistant turn,
# so memory matches what the user actually saw (security-audit P2-1).
_TELEGRAM_MAX_TEXT = 4096
_TRUNCATION_MARKER = "\n…[truncated]"


def _truncate_for_telegram(text: str) -> str:
    """Cap text at Telegram's 4096-char message limit, marking truncation."""
    if len(text) <= _TELEGRAM_MAX_TEXT:
        return text
    keep = _TELEGRAM_MAX_TEXT - len(_TRUNCATION_MARKER)
    return text[:keep] + _TRUNCATION_MARKER


def _load_env() -> tuple[str, set[int]]:
    """Read and validate TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_CHAT_IDS from env.

    Raises ConfigError on any missing, empty, or malformed value.
    Returns (token, allowlist_set).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN env var is required but missing or empty"
        )

    raw_ids = os.environ.get("ALLOWED_TELEGRAM_CHAT_IDS", "").strip()
    if not raw_ids:
        raise ConfigError(
            "ALLOWED_TELEGRAM_CHAT_IDS env var is required but missing or empty"
        )

    allowlist: set[int] = set()
    for segment in raw_ids.split(","):
        s = segment.strip()
        if not s:
            continue
        try:
            allowlist.add(int(s))
        except ValueError:
            raise ConfigError(
                f"ALLOWED_TELEGRAM_CHAT_IDS contains invalid integer: {s!r}"
            )

    if not allowlist:
        raise ConfigError(
            "ALLOWED_TELEGRAM_CHAT_IDS contained no valid chat IDs after parsing"
        )

    _log.info("Allowlist loaded: %d chat ID(s)", len(allowlist))
    return token, allowlist


def _is_allowed(chat_id: int, allowlist: set[int]) -> bool:
    """Return True iff chat_id is in the allowlist.

    Pure: no logging, no exceptions. The caller logs auth-drop with the
    chat_id. Kept private and side-effect-free so the allowlist check is
    testable in isolation without mocking the Telegram library.
    """
    return chat_id in allowlist


def _make_handler(
    allowlist: set[int],
    resolved: ResolvedProvider,
    system_prompt: str,
    memory: Memory,
    fallbacks: list[ResolvedProvider] | None = None,
):
    """Build the inbound-message handler closure.

    Factored out (rather than nested inside `run()`) so unit tests can
    construct the handler with mocks and invoke it directly without
    starting the polling loop. The plan calls this an inline async
    function; in practice we need a handle on it for tests.
    """

    async def _handle(update: Update, _context) -> None:
        # Whole-handler try/except (eng-review C1): a failure in memory,
        # the LLM call, or reply-send must NOT crash the polling loop.
        # Catch Exception (not BaseException) so KeyboardInterrupt/
        # SystemExit still unwind cleanly via pgttb's signal handlers.
        try:
            chat = update.effective_chat
            message = update.effective_message
            if chat is None or message is None or message.text is None:
                return
            chat_id = chat.id
            text = message.text

            # Auth check FIRST — before any memory write or LLM call
            # (CONNECTOR-AUDIT #3 + eng-review T1: blocked users get no
            # data storage, no provider call, no reply, no acknowledgement).
            if not _is_allowed(chat_id, allowlist):
                _log.info("auth-drop: chat_id=%d", chat_id)
                return

            memory.append(chat_id, "user", text)
            history = memory.history(chat_id)
            try:
                reply_text = await runtime.reply_with_fallback(
                    resolved, fallbacks or [], system_prompt, history, text, chat_id
                )
            except runtime.AllProvidersFailed:
                # Every configured provider failed. Send a single user-visible
                # status line and persist nothing. The CRITICAL log inside
                # reply_with_fallback already recorded the chain.
                outbound = (
                    "Having trouble reaching the LLM right now, "
                    "please try again in a minute."
                )
                await message.reply_text(outbound)
                _log.info(
                    "reply-fallback-exhausted: chat_id=%d reply_len=%d",
                    chat_id,
                    len(outbound),
                )
                return
            # Truncate BEFORE persisting + sending so memory matches what the
            # user saw. If we appended the full LLM reply and only truncated
            # the send, the next turn would re-condition on the long text and
            # the bot would look stuck (security-audit P2-1).
            outbound = _truncate_for_telegram(reply_text)
            memory.append(chat_id, "assistant", outbound)
            await message.reply_text(outbound)
            # reply_len only — never the reply text itself (eng-review T3).
            _log.info("reply-sent: chat_id=%d reply_len=%d", chat_id, len(outbound))
        except Exception:
            # exc_info=True; the L0 scrubbing logger redacts any registered
            # secrets (including the bot token) that might appear in the
            # traceback (e.g. a Telegram API URL).
            _log.error("handler failed", exc_info=True)

    return _handle


def run(
    config: Config,
    registry_resolver: Callable[[Config, str], ResolvedProvider],
    memory: Memory,
    fallbacks: list[ResolvedProvider] | None = None,
) -> None:
    """Long-running Telegram polling loop.

    SYNC, not async (eng-review A4): python-telegram-bot 22.x's
    Application.run_polling() owns its own event loop and registers
    SIGINT/SIGTERM handlers internally. The CLI entrypoint calls this
    function directly with no asyncio.run wrapper.
    """
    token, allowlist = _load_env()
    # Re-register the in-use token with the L0 scrubber. The default secret
    # snapshot happens at agent.logging import time; rotating TELEGRAM_BOT_TOKEN
    # without restarting the process would otherwise leave the new value
    # unsanitized in tracebacks (security-audit P2-3).
    register_secret(token)

    agent_spec = config.agents.get(_AGENT_GROUP)
    if agent_spec is None:
        raise ConfigError(
            f"Agent group {_AGENT_GROUP!r} not found in config; "
            f"defined groups: {list(config.agents.keys())}"
        )
    resolved = registry_resolver(config, agent_spec.model)

    # No token in this log line. Counts and identifiers only.
    _log.info(
        "Connector starting: provider=%s model=%s allowlist_size=%d fallback_len=%d",
        resolved.provider_name,
        resolved.model_id,
        len(allowlist),
        len(fallbacks or []),
    )

    app = ApplicationBuilder().token(token).build()
    handler = _make_handler(
        allowlist, resolved, agent_spec.system_prompt, memory, fallbacks
    )
    # filters.TEXT IS the 5MB attachment-size cap mechanism for 0.1 — non-text
    # is dropped before any handler runs (eng-review A3).
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)
