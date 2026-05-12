"""Unit tests for agent.init.validators.

All external HTTP is mocked at urllib.request.urlopen so tests run without
network access.
"""
from __future__ import annotations

import io
import json
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest

from agent.init.validators import (
    detect_provider_from_key,
    validate_provider_key,
    validate_telegram_token,
)


class _FakeResponse:
    """Minimal stand-in for the urlopen() context manager return value."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _ok_response(payload: dict) -> _FakeResponse:
    return _FakeResponse(200, json.dumps(payload).encode("utf-8"))


def _http_error(status: int, body: dict | None = None) -> urllib.error.HTTPError:
    payload = json.dumps(body or {}).encode("utf-8")
    return urllib.error.HTTPError(
        url="http://example.test",
        code=status,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(payload),
    )


# validate_telegram_token


def test_validate_telegram_token_success():
    payload = {"ok": True, "result": {"id": 1, "username": "kayatestbot"}}
    with patch("agent.init.validators.urllib.request.urlopen", return_value=_ok_response(payload)):
        result = validate_telegram_token("123:abc")
    assert result.ok is True
    assert result.error is None
    assert result.data == {"username": "kayatestbot"}


def test_validate_telegram_token_rejects_empty():
    result = validate_telegram_token("")
    assert result.ok is False
    assert "empty" in (result.error or "").lower()


def test_validate_telegram_token_strips_whitespace():
    payload = {"ok": True, "result": {"username": "bot"}}
    with patch("agent.init.validators.urllib.request.urlopen", return_value=_ok_response(payload)):
        result = validate_telegram_token("  123:abc  \n")
    assert result.ok is True


def test_validate_telegram_token_401():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(401, {"ok": False, "description": "Unauthorized"}),
    ):
        result = validate_telegram_token("badtoken")
    assert result.ok is False
    assert "rejected" in (result.error or "").lower()


def test_validate_telegram_token_404():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(404, {"ok": False, "description": "Not Found"}),
    ):
        result = validate_telegram_token("missing")
    assert result.ok is False
    assert "rejected" in (result.error or "").lower()


def test_validate_telegram_token_network_failure():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=urllib.error.URLError("dns error"),
    ):
        result = validate_telegram_token("123:abc")
    assert result.ok is False
    assert "network" in (result.error or "").lower()


def test_validate_telegram_token_malformed_body():
    bad = _FakeResponse(200, b"not json at all")
    with patch("agent.init.validators.urllib.request.urlopen", return_value=bad):
        result = validate_telegram_token("123:abc")
    # Malformed body parses to empty dict, ok flag missing -> failure.
    assert result.ok is False


def test_validate_telegram_token_missing_username():
    payload = {"ok": True, "result": {"id": 1}}
    with patch("agent.init.validators.urllib.request.urlopen", return_value=_ok_response(payload)):
        result = validate_telegram_token("123:abc")
    assert result.ok is False
    assert "username" in (result.error or "").lower()


# validate_provider_key


def test_validate_provider_key_success():
    payload = {"data": [{"id": "model-1"}]}
    with patch("agent.init.validators.urllib.request.urlopen", return_value=_ok_response(payload)):
        result = validate_provider_key("https://api.example.test/v1", "sk-abc")
    assert result.ok is True


def test_validate_provider_key_trailing_slash_normalised():
    payload = {"data": []}
    captured: dict[str, str] = {}

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["url"] = req.full_url
        return _ok_response(payload)

    with patch("agent.init.validators.urllib.request.urlopen", side_effect=fake_urlopen):
        validate_provider_key("https://api.example.test/v1/", "sk-abc")
    assert captured["url"] == "https://api.example.test/v1/models"


def test_validate_provider_key_sends_bearer_auth():
    payload = {"data": []}
    captured: dict[str, dict[str, str]] = {}

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["headers"] = dict(req.header_items())
        return _ok_response(payload)

    with patch("agent.init.validators.urllib.request.urlopen", side_effect=fake_urlopen):
        validate_provider_key("https://api.example.test/v1", "sk-abc")
    assert captured["headers"].get("Authorization") == "Bearer sk-abc"


def test_validate_provider_key_401():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(401, {"error": "invalid"}),
    ):
        result = validate_provider_key("https://api.example.test/v1", "bad")
    assert result.ok is False
    assert "401" in (result.error or "")


def test_validate_provider_key_403():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(403, {"error": "no credit"}),
    ):
        result = validate_provider_key("https://api.example.test/v1", "ok-key")
    assert result.ok is False
    assert "403" in (result.error or "")


def test_validate_provider_key_500():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(500, {"error": "boom"}),
    ):
        result = validate_provider_key("https://api.example.test/v1", "ok-key")
    assert result.ok is False
    assert "500" in (result.error or "")


def test_validate_provider_key_network_failure():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = validate_provider_key("https://api.example.test/v1", "ok-key")
    assert result.ok is False
    assert "network" in (result.error or "").lower()


def test_validate_provider_key_rejects_empty_key():
    result = validate_provider_key("https://api.example.test/v1", "")
    assert result.ok is False


def test_validate_provider_key_rejects_empty_base_url():
    result = validate_provider_key("", "sk-abc")
    assert result.ok is False


# detect_provider_from_key


@pytest.mark.parametrize(
    "key, expected",
    [
        ("sk-or-v1-abc", "openrouter"),
        ("gsk_abc123", "groq"),
        ("sk-anythingelse", "openai-suspect"),
        ("", None),
        ("   ", None),
        ("abcdef-no-prefix", None),
        ("deepinfra-style-random-string", None),
    ],
)
def test_detect_provider_from_key(key, expected):
    assert detect_provider_from_key(key) == expected


def test_detect_provider_openrouter_wins_over_openai_prefix():
    # sk-or-* must NOT classify as openai-suspect even though sk- is a prefix.
    assert detect_provider_from_key("sk-or-v1-xxxxx") == "openrouter"
