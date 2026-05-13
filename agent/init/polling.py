"""Long-poll Telegram getUpdates to capture the first chat ID.

Pure state machine. No print(), no input(). cli.py drives the loop and
handles user-facing messaging. time.sleep and time.monotonic are
parameterised so tests can advance the clock without waiting.

Telegram rate limit: one concurrent long-poll per bot token. The loop
here is sequential with a short server-side timeout, so we stay well
inside that limit.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

_BASE = "https://api.telegram.org"
_HTTP_SOCKET_TIMEOUT = 10
_SERVER_LONG_POLL_TIMEOUT = 2
_NETWORK_RETRY_SLEEP = 1.0
_DEFAULT_DEADLINE_SECONDS = 300


class PollTimeout(Exception):
    """Raised by poll_for_chat_id when the deadline expires without capture."""


def _fetch_updates(token: str, offset: int, server_timeout: int) -> list[dict]:
    """One getUpdates call. Returns the list of updates or raises URLError."""
    url = (
        f"{_BASE}/bot{token}/getUpdates"
        f"?offset={offset}&timeout={server_timeout}&limit=1"
    )
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=_HTTP_SOCKET_TIMEOUT) as resp:
        body = resp.read().decode("utf-8")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        return []
    result = parsed.get("result") or []
    return result if isinstance(result, list) else []


def _extract_chat_id(update: dict[str, Any]) -> int | None:
    """Return chat_id for normal messages. None for edited messages,
    channel posts, callback queries, and other non-message updates we
    cannot interpret as 'the user sent the bot a message'.
    """
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    return chat_id if isinstance(chat_id, int) else None


def clear_backlog(token: str) -> int:
    """Acknowledge any stale updates. Returns the offset to start polling from.

    A bot token sometimes carries leftover updates from earlier tests or
    other clients. If we did not clear them, the first poll would return
    a stale message and we would capture the wrong chat_id.
    """
    try:
        updates = _fetch_updates(token, offset=-1, server_timeout=0)
    except urllib.error.URLError:
        return 0
    if not updates:
        return 0
    last_update_id = updates[-1].get("update_id", 0)
    return int(last_update_id) + 1


def poll_for_chat_id(
    token: str,
    offset: int,
    timeout_seconds: int = _DEFAULT_DEADLINE_SECONDS,
    sleep_fn: Callable[[float], None] | None = None,
    monotonic_fn: Callable[[], float] | None = None,
) -> tuple[int, int]:
    """Long-poll until a message arrives. Returns (chat_id, next_offset).

    On a non-message update (edited, channel post, etc), the offset is
    advanced past it and polling continues. On URLError, we sleep
    _NETWORK_RETRY_SLEEP and retry without resetting the wall-clock
    deadline. Raises PollTimeout if the deadline elapses.

    The caller passes the offset from clear_backlog. The returned
    next_offset is the offset AFTER the captured update; if the caller
    rejects the chat_id and wants to wait for a different one, it
    should call poll_for_chat_id again with this next_offset. The
    rejected update is not re-presented because it has already been
    acknowledged to Telegram.
    """
    if sleep_fn is None:
        sleep_fn = time.sleep
    if monotonic_fn is None:
        monotonic_fn = time.monotonic
    deadline = monotonic_fn() + timeout_seconds
    while monotonic_fn() < deadline:
        try:
            updates = _fetch_updates(
                token, offset=offset, server_timeout=_SERVER_LONG_POLL_TIMEOUT
            )
        except urllib.error.URLError:
            sleep_fn(_NETWORK_RETRY_SLEEP)
            continue
        if not updates:
            # Telegram normally holds the connection for the server-side
            # timeout. If it returns instantly with an empty list (proxy,
            # transient state), pause briefly so we do not spin.
            sleep_fn(_NETWORK_RETRY_SLEEP)
            continue
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            chat_id = _extract_chat_id(update)
            if chat_id is not None:
                return chat_id, offset
            # Non-message update: offset has been advanced, keep polling
            # so the next loop iteration asks for the next update.
    raise PollTimeout()
