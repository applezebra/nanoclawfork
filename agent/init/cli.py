"""Top-level flow for `python3 -m agent init`.

Walks a non-developer through their first kayaclaw install: provider key,
Telegram bot token, chat ID capture, then writes .env and config.yaml.
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path
from typing import Callable

from agent.init.polling import PollTimeout, clear_backlog, poll_for_chat_id
from agent.init.validators import (
    ValidationResult,
    detect_provider_from_key,
    validate_provider_key,
    validate_telegram_token,
)
from agent.init.writers import render_config, render_env, write_files

_MAX_ATTEMPTS = 3

# Mirrors config.example.yaml; drift between the two is enforced by tests.
# `prefix_label` is set on providers whose API key has a published prefix
# regex (validators.detect_provider_from_key uses the same mapping). dict
# insertion order doubles as the picker order, so adding a provider here
# is a one-line edit.
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "meta-llama/llama-3.3-70b-instruct",
        "label": "OpenRouter (openrouter.ai, one key, many models)",
        "prefix_label": "OpenRouter",
    },
    "deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "label": "DeepInfra (deepinfra.com)",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
        "label": "Groq (groq.com)",
        "prefix_label": "Groq",
    },
}


def _prompt(text: str) -> str:
    """Single point of input() for testability."""
    return input(text).strip()


def _prompt_secret(text: str) -> str:
    """Like _prompt but does not echo. Used for API keys and bot tokens
    so secrets do not land in terminal scrollback or multiplexer buffers.
    """
    return getpass.getpass(text).strip()


def _confirm(text: str, default_yes: bool = True) -> bool:
    suffix = " [Y/n]" if default_yes else " [y/N]"
    raw = _prompt(text + suffix + ": ").lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _select_provider_from_list() -> tuple[str, dict[str, str]]:
    """Show numbered list, return (provider_key, provider_config_dict).

    For 'Other', returns ('custom', user-supplied dict).
    """
    keys = list(PROVIDER_DEFAULTS)
    other_choice = len(keys) + 1
    print()
    print("Pick your AI provider:")
    for i, key in enumerate(keys, start=1):
        print(f"  {i}. {PROVIDER_DEFAULTS[key]['label']}")
    print(f"  {other_choice}. Other (any OpenAI-compatible endpoint)")
    while True:
        raw = _prompt(f"Enter a number [1-{other_choice}]: ")
        if not raw.isdigit():
            print("  Please enter a number.")
            continue
        choice = int(raw)
        if 1 <= choice <= len(keys):
            key = keys[choice - 1]
            return key, dict(PROVIDER_DEFAULTS[key])
        if choice == other_choice:
            return _prompt_custom_provider()
        print(f"  Please enter a number between 1 and {other_choice}.")


def _prompt_custom_provider() -> tuple[str, dict[str, str]]:
    print()
    print("Custom provider. Three values needed:")
    base_url = _prompt("  Base URL (e.g. https://api.example.com/v1): ")
    api_key_env = _prompt("  Env var name to hold the API key (e.g. MY_PROVIDER_KEY): ")
    default_model = _prompt("  Model name (e.g. llama-3.3-70b): ")
    return "custom", {
        "base_url": base_url,
        "api_key_env": api_key_env,
        "default_model": default_model,
        "label": f"Custom ({base_url})",
    }


def _retry_validator(
    prompt_text: str,
    validator: Callable[[str], ValidationResult],
    failure_advice: str,
    prompter: Callable[[str], str] = _prompt,
) -> tuple[str, ValidationResult]:
    """Run validator up to _MAX_ATTEMPTS times. Returns (raw_value, result).

    On all-attempts-failed, exits the process with code 1. Pass
    `prompter=_prompt_secret` to hide the typed value (API keys, tokens).
    """
    for attempt in range(_MAX_ATTEMPTS):
        value = prompter(prompt_text)
        result = validator(value)
        if result.ok:
            return value, result
        remaining = _MAX_ATTEMPTS - attempt - 1
        print(f"  Error: {result.error}")
        if remaining > 0:
            print(f"  Please try again ({remaining} attempt(s) remaining).")
    print(f"  {_MAX_ATTEMPTS} attempts failed. {failure_advice}")
    sys.exit(1)


def _step_provider() -> tuple[str, dict[str, str], str]:
    """Returns (provider_key, provider_config_dict, api_key)."""
    print()
    print("Step 1 of 3: AI provider key.")
    print("(Your key is not echoed. Press Enter on an empty line to pick from a list.)")
    raw = _prompt_secret("Paste your AI provider API key: ")
    if raw:
        detected = detect_provider_from_key(raw)
        if detected in PROVIDER_DEFAULTS:
            provider_cfg = dict(PROVIDER_DEFAULTS[detected])
            prefix_label = provider_cfg.get("prefix_label", provider_cfg["label"])
            article = "an" if prefix_label[:1].lower() in "aeiou" else "a"
            if _confirm(f"Looks like {article} {prefix_label} key. Proceed?"):
                result = validate_provider_key(provider_cfg["base_url"], raw)
                if result.ok:
                    print(f"  Provider key valid for {provider_cfg['label']}.")
                    return detected, provider_cfg, raw
                print(f"  Error: {result.error}")
        elif detected == "openai-suspect":
            print("  That looks like it might be an OpenAI key. OpenAI is not")
            print("  in the supported list. Pick your provider from the list:")
        else:
            print("  Could not detect provider from the key format.")
    provider_key, provider_cfg = _select_provider_from_list()

    def _val(key: str) -> ValidationResult:
        return validate_provider_key(provider_cfg["base_url"], key)

    api_key, _result = _retry_validator(
        f"Paste your {provider_cfg['label']} API key (not echoed): ",
        _val,
        "Check the provider's API key dashboard.",
        prompter=_prompt_secret,
    )
    print(f"  Provider key valid for {provider_cfg['label']}.")
    return provider_key, provider_cfg, api_key


def _step_telegram_token() -> tuple[str, str]:
    """Returns (token, bot_username)."""
    print()
    print("Step 2 of 3: Telegram bot token.")
    print("Create a bot via @BotFather on Telegram if you have not already.")
    token, result = _retry_validator(
        "Paste your Telegram bot token (not echoed): ",
        validate_telegram_token,
        "Check the token from @BotFather on Telegram.",
        prompter=_prompt_secret,
    )
    bot_username = (result.data or {}).get("username", "")
    print(f"  Telegram bot token valid. Your bot is @{bot_username}.")
    return token, bot_username


def _prompt_manual_chat_id() -> int:
    while True:
        raw = _prompt("Enter your Telegram chat ID (integer): ")
        try:
            return int(raw)
        except ValueError:
            print("  Not an integer. Try again or press Ctrl-C to cancel.")


def _step_chat_id(token: str, bot_username: str) -> int:
    print()
    print("Step 3 of 3: Telegram chat ID.")
    print(f"Open Telegram, find @{bot_username}, and send it any message.")
    print("Waiting up to 5 minutes. Press Ctrl-C to enter the chat ID manually.")
    offset = clear_backlog(token)
    while True:
        try:
            chat_id, offset = poll_for_chat_id(token, offset)
        except PollTimeout:
            print("  No message received in 5 minutes.")
            if _confirm("Enter chat ID manually?", default_yes=True):
                return _prompt_manual_chat_id()
            print("  Waiting again...")
            continue
        except KeyboardInterrupt:
            print("\n  Capture cancelled.")
            if _confirm("Enter chat ID manually?", default_yes=True):
                return _prompt_manual_chat_id()
            print("  Waiting again...")
            continue
        print()
        print(f"  Got chat ID: {chat_id}")
        print("  This will be the only account allowed to talk to your bot.")
        if _confirm("Use this chat ID?"):
            return chat_id
        print("  Discarded. Waiting for a different message...")


def _step_existing_files() -> bool:
    """Check for existing .env / config.yaml. Returns True if init should
    continue (overwrite branch), False if init should exit (keep or cancel).
    """
    env_exists = Path(".env").exists()
    cfg_exists = Path("config.yaml").exists()
    if not (env_exists or cfg_exists):
        return True
    print()
    print("I found an existing setup:")
    print(f"  .env         {'EXISTS' if env_exists else 'not found'}")
    print(f"  config.yaml  {'EXISTS' if cfg_exists else 'not found'}")
    print()
    print("What do you want to do?")
    print("  1. Keep current files and exit.")
    print("  2. Overwrite both files and continue setup.")
    print("  3. Cancel.")
    while True:
        raw = _prompt("Enter a number [1-3]: ")
        if raw == "1":
            print("Your existing setup is unchanged.")
            return False
        if raw == "2":
            return True
        if raw == "3":
            print("Setup cancelled. Nothing was changed.")
            return False
        print("  Please enter 1, 2, or 3.")


def _step_write(
    token: str,
    chat_id: int,
    provider_cfg: dict[str, str],
    api_key: str,
    provider_key: str,
) -> None:
    env_content = render_env(token, chat_id, provider_cfg["api_key_env"], api_key)
    cfg_content = render_config(
        provider_key,
        provider_cfg["base_url"],
        provider_cfg["api_key_env"],
        provider_cfg["default_model"],
    )
    try:
        write_files(env_content, cfg_content)
    except Exception as e:
        print(f"  Error writing config: {e}", file=sys.stderr)
        raise
    print()
    print("All set. Now run:")
    print()
    print("    docker compose up -d")
    print()
    print("On Linux you may need `sudo` if you have not added yourself")
    print("to the docker group.")


def _check_docker_presence() -> None:
    """Non-blocking probe (FR-9 / ADR-6). Logs a one-line note if missing."""
    try:
        subprocess.run(
            ["docker", "--version"], capture_output=True, timeout=5, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        print()
        print("Note: docker was not found on PATH. Install it before the final step.")


def main() -> int:
    """Entry point for `python3 -m agent init`. Returns process exit code."""
    print("kayaclaw init")
    print("Walk through four questions to set up your first install.")
    _check_docker_presence()
    if not _step_existing_files():
        return 0
    provider_key, provider_cfg, api_key = _step_provider()
    token, bot_username = _step_telegram_token()
    chat_id = _step_chat_id(token, bot_username)
    _step_write(token, chat_id, provider_cfg, api_key, provider_key)
    return 0
