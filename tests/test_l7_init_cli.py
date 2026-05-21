"""Unit tests for agent.init.cli helpers.

Pins the small, deterministic pieces: status->headline mapping, the
diagnostics renderer, the retry menu, and the retry-after-fix loop.
The full init flow is exercised separately in test_l7_init_integration.
"""
from __future__ import annotations

from collections import deque
from unittest.mock import patch

import pytest

from agent.init.cli import (
    _PROVIDER_HEADLINES,
    _TELEGRAM_HEADLINES,
    _headline_for,
    _render_diagnostics,
    _retry_menu,
    _retry_validator,
)
from agent.init.validators import ValidationResult


def _queue(*values: str):
    q = deque(values)

    def fake(_prompt_text: str = "") -> str:
        if not q:
            raise AssertionError(f"input/prompt called past queue; last prompt={_prompt_text!r}")
        return q.popleft()

    return fake, q


def _ok(value_data: dict | None = None) -> ValidationResult:
    return ValidationResult(
        ok=True, error=None, data=value_data or {}, status_code=200, endpoint="x", body_snippet="",
    )


def _fail(status: int | None = 401, body: str | None = "bad") -> ValidationResult:
    return ValidationResult(
        ok=False, error="rejected", data=None,
        status_code=status, endpoint="https://api.example.test/v1/models",
        body_snippet=body,
    )


# headline mapping


def test_headline_for_known_provider_status():
    assert _headline_for(401, _PROVIDER_HEADLINES).startswith("Provider rejected")
    assert "no usage credit" in _headline_for(402, _PROVIDER_HEADLINES)
    assert "suspended" in _headline_for(403, _PROVIDER_HEADLINES)
    assert "wrong base_url" in _headline_for(404, _PROVIDER_HEADLINES)
    assert "rate-limit" in _headline_for(429, _PROVIDER_HEADLINES)


def test_headline_for_5xx_uses_5xx_bucket():
    assert _headline_for(500, _PROVIDER_HEADLINES) == _PROVIDER_HEADLINES["5xx"]
    assert _headline_for(503, _PROVIDER_HEADLINES) == _PROVIDER_HEADLINES["5xx"]
    assert _headline_for(599, _PROVIDER_HEADLINES) == _PROVIDER_HEADLINES["5xx"]


def test_headline_for_network_failure_uses_network_sentinel():
    assert _headline_for(None, _PROVIDER_HEADLINES) == _PROVIDER_HEADLINES["network"]
    assert _headline_for(None, _TELEGRAM_HEADLINES) == _TELEGRAM_HEADLINES["network"]


def test_headline_for_unknown_status_falls_to_other():
    assert _headline_for(418, _PROVIDER_HEADLINES) == _PROVIDER_HEADLINES["other"]
    assert _headline_for(305, _PROVIDER_HEADLINES) == _PROVIDER_HEADLINES["other"]


def test_telegram_headlines_have_distinct_401_404_share_message():
    # Spec §5 maps both 401 and 404 to the same Telegram headline.
    assert _TELEGRAM_HEADLINES[401] == _TELEGRAM_HEADLINES[404]


# diagnostics renderer


def _failure(
    status: int | None,
    endpoint: str | None,
    snippet: str | None = None,
) -> ValidationResult:
    return ValidationResult(
        ok=False,
        error="ignored",
        data=None,
        status_code=status,
        endpoint=endpoint,
        body_snippet=snippet,
    )


def test_render_diagnostics_404_with_snippet(capsys):
    result = _failure(
        404,
        "https://openrouter.ai/api/v1/models",
        '{"error":"not found","code":"restricted_endpoint"}',
    )
    _render_diagnostics(result, _PROVIDER_HEADLINES)
    out = capsys.readouterr().out
    assert "wrong base_url" in out
    assert "HTTP 404 from https://openrouter.ai/api/v1/models" in out
    assert "restricted_endpoint" in out


def test_render_diagnostics_5xx_bucket(capsys):
    result = _failure(503, "https://api.example.test/v1/models", "Service Unavailable")
    _render_diagnostics(result, _PROVIDER_HEADLINES)
    out = capsys.readouterr().out
    assert "server error" in out
    assert "HTTP 503" in out
    assert "Service Unavailable" in out


def test_render_diagnostics_network_failure(capsys):
    result = _failure(None, "https://api.example.test/v1/models", None)
    _render_diagnostics(result, _PROVIDER_HEADLINES)
    out = capsys.readouterr().out
    assert "Network failure" in out
    assert "api.example.test" in out
    # No "HTTP <num>" line when status is None.
    assert "HTTP " not in out


def test_render_diagnostics_no_snippet(capsys):
    result = _failure(401, "https://api.example.test/v1/models", None)
    _render_diagnostics(result, _PROVIDER_HEADLINES)
    out = capsys.readouterr().out
    assert "Provider rejected" in out
    # Body block should be absent when snippet is None or empty.
    lines = out.strip().splitlines()
    indented = [ln for ln in lines if ln.startswith("      ")]
    assert indented == []


def test_render_diagnostics_attempt_counter(capsys):
    result = _failure(429, "https://api.example.test/v1/models", None)
    _render_diagnostics(result, _PROVIDER_HEADLINES, attempt=3)
    out = capsys.readouterr().out
    assert "(attempt 3)" in out


def test_render_diagnostics_no_attempt_omits_counter(capsys):
    result = _failure(429, "https://api.example.test/v1/models", None)
    _render_diagnostics(result, _PROVIDER_HEADLINES)
    out = capsys.readouterr().out
    assert "attempt" not in out


def test_render_diagnostics_multiline_snippet_indented(capsys):
    result = _failure(
        500,
        "https://api.example.test/v1/models",
        "line one\nline two\nline three",
    )
    _render_diagnostics(result, _PROVIDER_HEADLINES)
    out = capsys.readouterr().out
    assert "      line one" in out
    assert "      line two" in out
    assert "      line three" in out


def test_render_diagnostics_telegram_uses_telegram_headlines(capsys):
    result = _failure(
        401,
        "https://api.telegram.org/bot12345:abc/getMe",
        '{"ok":false,"description":"Unauthorized"}',
    )
    _render_diagnostics(result, _TELEGRAM_HEADLINES)
    out = capsys.readouterr().out
    assert "Telegram rejected" in out
    assert "Unauthorized" in out


# retry menu


@pytest.mark.parametrize("typed,expected", [
    ("", "r"),
    ("r", "r"),
    ("R", "r"),
    ("n", "n"),
    ("N", "n"),
    ("q", "q"),
    ("Q", "q"),
])
def test_retry_menu_accepts_canonical_inputs(typed, expected, capsys):
    fake, _q = _queue(typed)
    with patch("agent.init.cli._prompt", side_effect=fake):
        assert _retry_menu() == expected


def test_retry_menu_reprompts_on_unknown(capsys):
    fake, _q = _queue("x", "huh", "r")
    with patch("agent.init.cli._prompt", side_effect=fake):
        assert _retry_menu() == "r"
    out = capsys.readouterr().out
    # Two unknown inputs -> two reprompt messages.
    assert out.count("Please type r, n, or q.") == 2


# retry-after-fix loop


def test_retry_validator_succeeds_on_first_try(capsys):
    val_responses = deque([_ok()])

    def validator(_v: str) -> ValidationResult:
        return val_responses.popleft()

    fake, _q = _queue("paste-value")
    with patch("agent.init.cli._prompt", side_effect=fake):
        value, result = _retry_validator(
            "Paste key: ", validator, _PROVIDER_HEADLINES, prompter=fake,
        )
    assert value == "paste-value"
    assert result.ok is True


def test_retry_validator_same_value_after_fix_succeeds(capsys):
    """[r] reuses the prior value -> simulates user fixing upstream and retrying."""
    val_responses = deque([_fail(), _ok()])

    def validator(v: str) -> ValidationResult:
        return val_responses.popleft()

    # First call: prompter for initial value; second call: menu returns 'r'.
    prompter, _q1 = _queue("the-key")
    menu, _q2 = _queue("r")
    with patch("agent.init.cli._prompt", side_effect=menu):
        value, result = _retry_validator(
            "Paste key: ", validator, _PROVIDER_HEADLINES, prompter=prompter,
        )
    assert value == "the-key"
    assert result.ok is True


def test_retry_validator_new_value_after_failure(capsys):
    """[n] re-prompts and validator sees a fresh value."""
    val_responses = deque([_fail(), _ok()])
    seen = []

    def validator(v: str) -> ValidationResult:
        seen.append(v)
        return val_responses.popleft()

    prompter, _q1 = _queue("first-bad", "second-good")
    menu, _q2 = _queue("n")
    with patch("agent.init.cli._prompt", side_effect=menu):
        value, result = _retry_validator(
            "Paste key: ", validator, _PROVIDER_HEADLINES, prompter=prompter,
        )
    assert seen == ["first-bad", "second-good"]
    assert value == "second-good"
    assert result.ok is True


def test_retry_validator_quits_raises_keyboard_interrupt():
    """[q] raises KeyboardInterrupt so main() can clean-exit."""
    val_responses = deque([_fail()])

    def validator(_v: str) -> ValidationResult:
        return val_responses.popleft()

    prompter, _q1 = _queue("key")
    menu, _q2 = _queue("q")
    with patch("agent.init.cli._prompt", side_effect=menu):
        with pytest.raises(KeyboardInterrupt):
            _retry_validator(
                "Paste key: ", validator, _PROVIDER_HEADLINES, prompter=prompter,
            )


def test_retry_validator_no_cap_seven_failures_then_success():
    """No attempt cap: seven fails then a success works."""
    val_responses = deque([_fail()] * 7 + [_ok()])

    def validator(_v: str) -> ValidationResult:
        return val_responses.popleft()

    prompter, _q1 = _queue("key")
    menu, _q2 = _queue("r", "r", "r", "r", "r", "r", "r")
    with patch("agent.init.cli._prompt", side_effect=menu):
        value, result = _retry_validator(
            "Paste key: ", validator, _PROVIDER_HEADLINES, prompter=prompter,
        )
    assert result.ok is True


def test_retry_validator_initial_value_skips_first_prompt():
    """initial_value short-circuits the first prompter call.

    Simulates the early-happy-path: user already pasted a key at the
    top of _step_provider; that key flows in as initial_value and gets
    validated without re-prompting.
    """
    val_responses = deque([_ok()])

    def validator(v: str) -> ValidationResult:
        return val_responses.popleft()

    def prompter(_p: str) -> str:
        raise AssertionError("prompter should not be called when initial_value is set")

    value, result = _retry_validator(
        "Paste key: ", validator, _PROVIDER_HEADLINES,
        prompter=prompter, initial_value="pre-pasted-key",
    )
    assert value == "pre-pasted-key"
    assert result.ok is True


def test_retry_validator_initial_value_then_fail_then_new(capsys):
    """Early-happy-path regression: initial_value fails, user picks [n], gives new value."""
    val_responses = deque([_fail(), _ok()])
    seen = []

    def validator(v: str) -> ValidationResult:
        seen.append(v)
        return val_responses.popleft()

    prompter, _q1 = _queue("user-types-replacement")
    menu, _q2 = _queue("n")
    with patch("agent.init.cli._prompt", side_effect=menu):
        value, result = _retry_validator(
            "Paste key: ", validator, _PROVIDER_HEADLINES,
            prompter=prompter, initial_value="pre-pasted-key",
        )
    assert seen == ["pre-pasted-key", "user-types-replacement"]
    assert value == "user-types-replacement"


def test_retry_validator_renders_attempt_counter(capsys):
    val_responses = deque([_fail(), _fail(), _ok()])

    def validator(_v: str) -> ValidationResult:
        return val_responses.popleft()

    prompter, _q1 = _queue("key")
    menu, _q2 = _queue("r", "r")
    with patch("agent.init.cli._prompt", side_effect=menu):
        _retry_validator(
            "Paste key: ", validator, _PROVIDER_HEADLINES, prompter=prompter,
        )
    out = capsys.readouterr().out
    assert "(attempt 1)" in out
    assert "(attempt 2)" in out
