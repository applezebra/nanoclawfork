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
    BODY_SNIPPET_MAX_CHARS,
    _scrub_secrets,
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

    def read(self, n: int | None = None) -> bytes:
        return self._body if n is None or n < 0 else self._body[:n]


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


# diagnostic fields (status_code, endpoint, body_snippet)


def test_provider_success_populates_status_endpoint_snippet():
    payload = {"data": []}
    with patch("agent.init.validators.urllib.request.urlopen", return_value=_ok_response(payload)):
        result = validate_provider_key("https://api.example.test/v1", "sk-abc")
    assert result.ok is True
    assert result.status_code == 200
    assert result.endpoint == "https://api.example.test/v1/models"
    assert result.body_snippet is not None  # may be "" if body was empty, but field exists


def test_provider_401_populates_diagnostics():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(401, {"error": "bad key"}),
    ):
        result = validate_provider_key("https://api.example.test/v1", "sk-abc")
    assert result.ok is False
    assert result.status_code == 401
    assert result.endpoint == "https://api.example.test/v1/models"
    assert "bad key" in (result.body_snippet or "")


def test_provider_404_populates_diagnostics():
    """Wei's OpenRouter guardrails case: 404 on /models with a body."""
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(404, {"error": "not found", "code": "restricted_endpoint"}),
    ):
        result = validate_provider_key("https://openrouter.ai/api/v1", "sk-or-v1-xxx")
    assert result.ok is False
    assert result.status_code == 404
    assert result.endpoint == "https://openrouter.ai/api/v1/models"
    assert "restricted_endpoint" in (result.body_snippet or "")


def test_provider_network_failure_endpoint_set_status_none():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=urllib.error.URLError("dns error"),
    ):
        result = validate_provider_key("https://api.example.test/v1", "sk-abc")
    assert result.ok is False
    assert result.status_code is None
    assert result.endpoint == "https://api.example.test/v1/models"
    assert result.body_snippet is None


def test_telegram_401_populates_body_snippet():
    """Regression: the 401/404 branch used to discard the body before diagnostics shipped."""
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(401, {"ok": False, "description": "Unauthorized"}),
    ):
        result = validate_telegram_token("12345:fake-bot-token-with-30-or-more-chars")
    assert result.ok is False
    assert result.status_code == 401
    assert result.endpoint == "https://api.telegram.org/bot12345:fake-bot-token-with-30-or-more-chars/getMe"
    assert "Unauthorized" in (result.body_snippet or "")


def test_telegram_404_populates_body_snippet():
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(404, {"ok": False, "description": "Not Found"}),
    ):
        result = validate_telegram_token("99999:fake-bot-token-with-30-or-more-chars")
    assert result.ok is False
    assert result.status_code == 404
    assert "Not Found" in (result.body_snippet or "")


def test_telegram_success_populates_diagnostics():
    payload = {"ok": True, "result": {"id": 1, "username": "bot"}}
    with patch("agent.init.validators.urllib.request.urlopen", return_value=_ok_response(payload)):
        result = validate_telegram_token("123:abc")
    assert result.ok is True
    assert result.status_code == 200
    assert "api.telegram.org" in (result.endpoint or "")


def test_body_snippet_capped_at_max_chars():
    """Body snippet is capped at BODY_SNIPPET_MAX_CHARS; oversized bodies are truncated."""
    big_body = ("X" * (BODY_SNIPPET_MAX_CHARS + 200)).encode("utf-8")
    err = urllib.error.HTTPError(
        url="http://e.test", code=500, msg="err",
        hdrs=None, fp=io.BytesIO(big_body),  # type: ignore[arg-type]
    )
    with patch("agent.init.validators.urllib.request.urlopen", side_effect=err):
        result = validate_provider_key("https://api.example.test/v1", "sk-abc")
    assert result.body_snippet is not None
    assert len(result.body_snippet) <= BODY_SNIPPET_MAX_CHARS


# secret scrubber


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abc1234567890abcdef",
        "gsk_abc123def456ghi789jklmno",
        "Bearer abc123def456ghi789jklmno",
        "xoxa-abcdef1234567890",
        "1234567890:AAFakeTelegramTokenWith30PlusChars_xyz",
    ],
)
def test_scrub_generic_patterns(secret: str):
    out = _scrub_secrets(f"prefix {secret} suffix")
    assert secret not in out
    assert "[REDACTED]" in out


def test_scrub_env_assignment_pattern():
    out = _scrub_secrets("API_KEY=supersecretvalue123")
    assert "supersecretvalue123" not in out
    assert "[REDACTED]" in out


def test_scrub_idempotent():
    once = _scrub_secrets("sk-abc1234567890abcdef something")
    twice = _scrub_secrets(once)
    assert once == twice


def test_scrub_leaves_clean_text_alone():
    out = _scrub_secrets("Provider returned HTTP 404. Not found.")
    assert out == "Provider returned HTTP 404. Not found."


def test_scrub_also_redact_catches_custom_keys():
    """A DeepInfra-shaped key (no published prefix) leaked in the body
    must be redacted via the also_redact list."""
    deepinfra_key = "0123456789abcdef0123456789abcdef"  # 32 chars, no prefix
    body = f'{{"error":"Bearer {deepinfra_key} is invalid"}}'
    out = _scrub_secrets(body, also_redact=[deepinfra_key])
    assert deepinfra_key not in out
    assert "[REDACTED]" in out


def test_scrub_also_redact_skips_short_literals():
    """A 1-char also_redact value would blanket the body. Floor is 8 chars."""
    out = _scrub_secrets("the quick brown fox", also_redact=["x"])
    # "x" is too short to redact; should appear intact.
    assert "fox" in out


def test_scrub_handles_empty_input():
    assert _scrub_secrets("") == ""
    assert _scrub_secrets("", also_redact=["whatever"]) == ""


def test_provider_snippet_redacts_user_key_in_body():
    """The user's pasted key, echoed in an error body, must not leak."""
    deepinfra_key = "0123456789abcdef0123456789abcdef"
    err_body = {"error": f"key {deepinfra_key} not authorised"}
    with patch(
        "agent.init.validators.urllib.request.urlopen",
        side_effect=_http_error(401, err_body),
    ):
        result = validate_provider_key("https://api.deepinfra.com/v1/openai", deepinfra_key)
    assert result.body_snippet is not None
    assert deepinfra_key not in result.body_snippet
    assert "[REDACTED]" in result.body_snippet
