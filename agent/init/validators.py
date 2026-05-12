"""HTTP validation against Telegram and OpenAI-compatible provider endpoints.

Pure functions. No input(), no print(). Each validator returns a
ValidationResult so cli.py can drive retry loops without HTTP details
leaking into the flow.

Uses urllib.request rather than httpx to avoid dragging in pydantic-ai
and the OpenAI SDK at init cold-start time (see ADR-1 in the plan).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import NamedTuple

_HTTP_TIMEOUT_SECONDS = 10


class ValidationResult(NamedTuple):
    ok: bool
    error: str | None
    data: dict | None


def _get_json(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    """GET url, parse JSON body. Returns (status_code, parsed_dict).

    Raises urllib.error.URLError on network failure, json.JSONDecodeError
    on malformed body. Non-2xx responses do NOT raise here; the caller
    decides how to interpret 401/403/etc.
    """
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        parsed = {}
    return status, parsed


def validate_telegram_token(token: str) -> ValidationResult:
    """Hit api.telegram.org/bot<TOKEN>/getMe. Returns bot username on success."""
    token = token.strip()
    if not token:
        return ValidationResult(False, "Token is empty.", None)
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        status, parsed = _get_json(url)
    except urllib.error.URLError as e:
        return ValidationResult(
            False,
            f"Could not reach api.telegram.org. Check your network. ({e.reason})",
            None,
        )
    if status == 200 and parsed.get("ok") is True:
        result = parsed.get("result") or {}
        username = result.get("username")
        if not username:
            return ValidationResult(False, "Telegram returned no bot username.", None)
        return ValidationResult(True, None, {"username": username})
    if status in (401, 404):
        return ValidationResult(
            False, "Telegram rejected the token. Check it was copied correctly.", None
        )
    description = parsed.get("description") if isinstance(parsed, dict) else None
    return ValidationResult(
        False,
        f"Telegram returned HTTP {status}: {description or 'unexpected response'}",
        None,
    )


def validate_provider_key(base_url: str, api_key: str) -> ValidationResult:
    """Hit <base_url>/models with Bearer auth. Any 200 means key is valid."""
    api_key = api_key.strip()
    base_url = base_url.rstrip("/")
    if not api_key:
        return ValidationResult(False, "API key is empty.", None)
    if not base_url:
        return ValidationResult(False, "Provider base_url is empty.", None)
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        status, parsed = _get_json(url, headers=headers)
    except urllib.error.URLError as e:
        return ValidationResult(
            False,
            f"Could not reach {base_url}. Check your network. ({e.reason})",
            None,
        )
    if status == 200:
        return ValidationResult(True, None, {"status": 200})
    if status in (401, 403):
        return ValidationResult(
            False,
            f"Provider rejected the key (HTTP {status}). Check it was copied "
            f"correctly and the account has credit.",
            None,
        )
    return ValidationResult(
        False,
        f"Provider returned HTTP {status} from {url}. Unexpected response.",
        None,
    )


# Maps published key prefix to the provider key used in cli.PROVIDER_DEFAULTS.
# Longer / more specific prefixes must appear first so OpenRouter (sk-or-)
# wins over the bare sk- branch below.
PROVIDER_KEY_PREFIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^sk-or-"), "openrouter"),
    (re.compile(r"^gsk_"), "groq"),
]


def detect_provider_from_key(api_key: str) -> str | None:
    """Return the matching provider key, 'openai-suspect', or None.

    'openai-suspect' signals a bare sk- prefix that did not match a known
    provider. OpenAI is not in the supported list, so cli.py treats this
    as 'fall through to the numbered list with a heads-up message'.
    """
    api_key = api_key.strip()
    if not api_key:
        return None
    for pattern, provider in PROVIDER_KEY_PREFIXES:
        if pattern.match(api_key):
            return provider
    if api_key.startswith("sk-"):
        return "openai-suspect"
    return None
