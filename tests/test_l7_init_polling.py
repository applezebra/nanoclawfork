"""Unit tests for agent.init.polling.

External HTTP and clock are both mocked so tests run instantly without
network or real waits.
"""
from __future__ import annotations

import json
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest

from agent.init.polling import (
    PollTimeout,
    _extract_chat_id,
    clear_backlog,
    poll_for_chat_id,
)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _resp(payload: dict) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


def _message_update(update_id: int, chat_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id, "type": "private"}, "text": "hi"},
    }


def _edited_update(update_id: int) -> dict:
    """Telegram update with no 'message' key (edited_message instead)."""
    return {
        "update_id": update_id,
        "edited_message": {"chat": {"id": 999, "type": "private"}, "text": "edited"},
    }


# _extract_chat_id


def test_extract_chat_id_from_message():
    assert _extract_chat_id(_message_update(1, 42)) == 42


def test_extract_chat_id_returns_none_for_edited_message():
    assert _extract_chat_id(_edited_update(1)) is None


def test_extract_chat_id_returns_none_for_missing_chat():
    assert _extract_chat_id({"update_id": 1, "message": {}}) is None


def test_extract_chat_id_returns_none_for_non_int_id():
    update = {"update_id": 1, "message": {"chat": {"id": "not-int"}}}
    assert _extract_chat_id(update) is None


# clear_backlog


def test_clear_backlog_empty_queue():
    with patch(
        "agent.init.polling.urllib.request.urlopen",
        return_value=_resp({"ok": True, "result": []}),
    ):
        assert clear_backlog("tok") == 0


def test_clear_backlog_returns_next_offset():
    payload = {"ok": True, "result": [_message_update(57, 100)]}
    with patch("agent.init.polling.urllib.request.urlopen", return_value=_resp(payload)):
        assert clear_backlog("tok") == 58


def test_clear_backlog_network_failure_returns_zero():
    with patch(
        "agent.init.polling.urllib.request.urlopen",
        side_effect=urllib.error.URLError("dns error"),
    ):
        assert clear_backlog("tok") == 0


# poll_for_chat_id


def _fake_clock():
    """Deterministic clock: each call advances by 1.0 second."""
    state = {"t": 0.0}

    def tick() -> float:
        state["t"] += 1.0
        return state["t"]

    return tick


def test_poll_returns_chat_id_on_first_message():
    payload = {"ok": True, "result": [_message_update(10, 555)]}
    with patch("agent.init.polling.urllib.request.urlopen", return_value=_resp(payload)):
        chat_id, offset = poll_for_chat_id(
            "tok", offset=0, timeout_seconds=60,
            sleep_fn=lambda _s: None, monotonic_fn=_fake_clock(),
        )
    assert chat_id == 555
    assert offset == 11


def test_poll_skips_non_message_update():
    """First update is edited_message (no chat_id); second is a real message."""
    responses = [
        _resp({"ok": True, "result": [_edited_update(5)]}),
        _resp({"ok": True, "result": [_message_update(6, 777)]}),
    ]
    with patch("agent.init.polling.urllib.request.urlopen", side_effect=responses):
        chat_id, offset = poll_for_chat_id(
            "tok", offset=0, timeout_seconds=60,
            sleep_fn=lambda _s: None, monotonic_fn=_fake_clock(),
        )
    assert chat_id == 777
    assert offset == 7


def test_poll_continues_on_empty_response():
    responses = [
        _resp({"ok": True, "result": []}),
        _resp({"ok": True, "result": []}),
        _resp({"ok": True, "result": [_message_update(99, 12345)]}),
    ]
    with patch("agent.init.polling.urllib.request.urlopen", side_effect=responses):
        chat_id, _ = poll_for_chat_id(
            "tok", offset=0, timeout_seconds=60,
            sleep_fn=lambda _s: None, monotonic_fn=_fake_clock(),
        )
    assert chat_id == 12345


def test_poll_retries_on_urlerror():
    responses = [
        urllib.error.URLError("connection lost"),
        _resp({"ok": True, "result": [_message_update(1, 42)]}),
    ]
    sleeps: list[float] = []
    with patch("agent.init.polling.urllib.request.urlopen", side_effect=responses):
        chat_id, _ = poll_for_chat_id(
            "tok", offset=0, timeout_seconds=60,
            sleep_fn=lambda s: sleeps.append(s),
            monotonic_fn=_fake_clock(),
        )
    assert chat_id == 42
    assert sleeps == [pytest.approx(1.0)]


def test_poll_raises_timeout_when_deadline_elapses():
    """monotonic_fn returns 0 first (start), then a huge value (deadline passed)."""
    times = iter([0.0, 9999.0])

    def fake_monotonic() -> float:
        return next(times)

    with patch(
        "agent.init.polling.urllib.request.urlopen",
        return_value=_resp({"ok": True, "result": []}),
    ):
        with pytest.raises(PollTimeout):
            poll_for_chat_id(
                "tok", offset=0, timeout_seconds=300,
                sleep_fn=lambda _s: None, monotonic_fn=fake_monotonic,
            )


def test_poll_offset_advances_after_decline_scenario():
    """P2-3 plan-eng-review case: caller receives chat_id A, rejects it,
    calls poll again with the returned offset. The same update is NOT
    re-presented; the next higher update_id is delivered.
    """
    responses = [
        _resp({"ok": True, "result": [_message_update(50, 100)]}),
        _resp({"ok": True, "result": [_message_update(51, 200)]}),
    ]
    with patch("agent.init.polling.urllib.request.urlopen", side_effect=responses):
        chat_id_a, offset_a = poll_for_chat_id(
            "tok", offset=0, timeout_seconds=60,
            sleep_fn=lambda _s: None, monotonic_fn=_fake_clock(),
        )
        chat_id_b, offset_b = poll_for_chat_id(
            "tok", offset=offset_a, timeout_seconds=60,
            sleep_fn=lambda _s: None, monotonic_fn=_fake_clock(),
        )
    assert (chat_id_a, offset_a) == (100, 51)
    assert (chat_id_b, offset_b) == (200, 52)


def test_poll_uses_offset_in_url():
    """Verify the offset query param is passed through to Telegram."""
    captured_urls: list[str] = []

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured_urls.append(req.full_url)
        return _resp({"ok": True, "result": [_message_update(7, 88)]})

    with patch("agent.init.polling.urllib.request.urlopen", side_effect=fake_urlopen):
        poll_for_chat_id(
            "tok", offset=42, timeout_seconds=60,
            sleep_fn=lambda _s: None, monotonic_fn=_fake_clock(),
        )
    assert "offset=42" in captured_urls[0]
