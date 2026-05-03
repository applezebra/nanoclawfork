"""Telegram polling connector — Steps 1-2: env-var validation + allowlist filter."""
from __future__ import annotations

import os

from agent.config import ConfigError
from agent.logging import get_logger

_log = get_logger("agent.connectors.telegram")


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
