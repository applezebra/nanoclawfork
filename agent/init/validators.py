"""HTTP validation against Telegram and OpenAI-compatible provider endpoints.

Pure functions. No input(), no print(). Each validator returns a
ValidationResult so cli.py can drive retry loops without HTTP details
leaking into the flow.

Uses urllib.request rather than httpx to avoid dragging in pydantic-ai
and the OpenAI SDK at init cold-start time (see ADR-1 in the L7 plan).

Each ValidationResult carries `status_code`, `endpoint`, and
`body_snippet` so the CLI can render actionable diagnostics and offer
retry-after-fix without re-prompting.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import NamedTuple

_HTTP_TIMEOUT_SECONDS = 10

# Cap on diagnostic snippets shown back to the user. Large enough to
# carry full provider error chains (URL paths, error codes), small enough
# that a hostile or misconfigured upstream can't flood the terminal.
BODY_SNIPPET_MAX_CHARS = 500
# Hard cap on the body actually read from the socket. 4x the snippet
# cap covers UTF-8 multibyte expansion and gives a small buffer for
# JSON parsing without ever buffering megabytes from a broken upstream.
_BODY_READ_LIMIT = BODY_SNIPPET_MAX_CHARS * 4


class ValidationResult(NamedTuple):
    ok: bool
    error: str | None
    data: dict | None
    # status_code is None for network-level failures (DNS, connection
    # refused, timeout) and for local-input rejections (empty token).
    status_code: int | None = None
    endpoint: str | None = None
    body_snippet: str | None = None


# Generic regexes for well-known key shapes. The per-call `also_redact`
# list catches the user's actual pasted value verbatim: keys without a
# published prefix (e.g. DeepInfra's 32-char alphanumeric) won't match
# anything here on their own.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\b\d{8,11}:[A-Za-z0-9_\-]{30,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"(?im)^[A-Z_]*(?:TOKEN|SECRET|API_KEY|PASSWORD)[A-Z_]*\s*=\s*\S+"),
)

_REDACTED = "[REDACTED]"


def _scrub_secrets(text: str, *, also_redact: list[str] | None = None) -> str:
    """Redact common secret patterns plus any caller-supplied literals.

    Idempotent. Safe on empty/None-like inputs. Use `also_redact` to
    redact the user's own pasted key, which generic patterns won't
    catch when the provider uses a non-standard key shape.
    """
    if not text:
        return ""
    out = text
    if also_redact:
        for literal in also_redact:
            # Only redact non-trivial literals: a 1-char "redact" would
            # blanket the whole body. 8 chars is the floor for any real
            # API key.
            if literal and len(literal) >= 8:
                out = re.sub(re.escape(literal), _REDACTED, out)
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_REDACTED, out)
    return out


def _snippet(body: str, *, also_redact: list[str] | None = None) -> str:
    """Truncate + scrub. Returns "" for empty input so the field stays
    serialisable (consumers can treat None vs "" as "no body" either way)."""
    if not body:
        return ""
    return _scrub_secrets(body[:BODY_SNIPPET_MAX_CHARS], also_redact=also_redact)


def _get_json(
    url: str, headers: dict[str, str] | None = None
) -> tuple[int, dict, str]:
    """GET url, parse JSON body. Returns (status_code, parsed_dict, raw_body).

    Raises urllib.error.URLError on network failure. Non-2xx HTTP
    responses do NOT raise: the body is captured for the caller to
    interpret. Malformed JSON parses to an empty dict but the raw_body
    is still returned so the caller can build a diagnostic snippet.
    """
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            status = resp.status
            body = resp.read(_BODY_READ_LIMIT).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read(_BODY_READ_LIMIT).decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body) if body else {}
        if not isinstance(parsed, dict):
            parsed = {}
    except json.JSONDecodeError:
        parsed = {}
    return status, parsed, body


def validate_telegram_token(token: str) -> ValidationResult:
    """Hit api.telegram.org/bot<TOKEN>/getMe. Returns bot username on success."""
    token = token.strip()
    if not token:
        return ValidationResult(False, "Token is empty.", None)
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        status, parsed, body = _get_json(url)
    except urllib.error.URLError as e:
        return ValidationResult(
            False,
            f"Could not reach api.telegram.org. Check your network. ({e.reason})",
            None,
            status_code=None,
            endpoint=url,
            body_snippet=None,
        )
    snippet = _snippet(body, also_redact=[token])
    if status == 200 and parsed.get("ok") is True:
        result = parsed.get("result") or {}
        username = result.get("username")
        if not username:
            return ValidationResult(
                False,
                "Telegram returned no bot username.",
                None,
                status_code=status,
                endpoint=url,
                body_snippet=snippet,
            )
        return ValidationResult(
            True,
            None,
            {"username": username},
            status_code=status,
            endpoint=url,
            body_snippet=snippet,
        )
    if status in (401, 404):
        return ValidationResult(
            False,
            "Telegram rejected the token. Check it was copied correctly.",
            None,
            status_code=status,
            endpoint=url,
            body_snippet=snippet,
        )
    description = parsed.get("description") if isinstance(parsed, dict) else None
    return ValidationResult(
        False,
        f"Telegram returned HTTP {status}: {description or 'unexpected response'}",
        None,
        status_code=status,
        endpoint=url,
        body_snippet=snippet,
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
        status, _parsed, body = _get_json(url, headers=headers)
    except urllib.error.URLError as e:
        return ValidationResult(
            False,
            f"Could not reach {base_url}. Check your network. ({e.reason})",
            None,
            status_code=None,
            endpoint=url,
            body_snippet=None,
        )
    snippet = _snippet(body, also_redact=[api_key])
    if status == 200:
        return ValidationResult(
            True,
            None,
            {"status": 200},
            status_code=status,
            endpoint=url,
            body_snippet=snippet,
        )
    if status in (401, 403):
        return ValidationResult(
            False,
            f"Provider rejected the key (HTTP {status}). Check it was copied "
            f"correctly and the account has credit.",
            None,
            status_code=status,
            endpoint=url,
            body_snippet=snippet,
        )
    return ValidationResult(
        False,
        f"Provider returned HTTP {status} from {url}. Unexpected response.",
        None,
        status_code=status,
        endpoint=url,
        body_snippet=snippet,
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
