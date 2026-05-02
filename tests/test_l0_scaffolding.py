"""L0 scaffolding tests.

Step 1: package skeleton and brand constants.
Step 2: token-scrubbing logger.
Step 3 (support files) tests are added in their own code-implementer invocation.
"""
import importlib
import io
import logging
import logging.handlers
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import agent
import agent.__about__ as __about__

PROJECT_ROOT = Path(__file__).parent.parent


# --- Step 1: package skeleton and brand constants ---


def test_agent_importable():
    """agent package must import without error."""
    # Import already happened at module load; verify the module object exists.
    assert agent is not None


def test_about_constants():
    """__brand__ must be a non-empty string; __version__ must start with '0.1'."""
    assert isinstance(__about__.__brand__, str) and __about__.__brand__
    assert __about__.__version__.startswith("0.1")


def test_brand_not_in_init_source():
    """Brand string must not appear in agent/__init__.py source text.

    Proxy check for NFR-BD1 (the real grep check runs in L6).
    Reads __init__.__file__ so the path stays correct regardless of install mode.
    """
    init_path = agent.__file__
    with open(init_path, encoding="utf-8") as fh:
        source = fh.read()
    assert __about__.__brand__ not in source, (
        f"Brand string '{__about__.__brand__}' must not appear in agent/__init__.py"
    )


def test_no_provider_sdk_is_a_hard_dependency():
    """No provider-specific SDK should be a hard dependency.

    Verifies the agnostic guarantee: installing our project does NOT force any
    specific LLM provider's SDK on the user. Providers are configured at runtime
    via config.yaml; the SDK that pydantic-ai uses to talk to them is generic.

    Concretely: our pyproject.toml must not list provider-vendor SDKs (anthropic,
    openai, google-generativeai, etc.) as hard dependencies. If any are needed
    for a specific provider integration, they belong in optional `[project.optional-dependencies]`
    extras (e.g. `nanoclawfork[anthropic]`) — never in the base install.
    """
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    declared = [d.lower() for d in pyproject["project"]["dependencies"]]

    forbidden_in_base = ["anthropic", "openai", "google-generativeai", "cohere", "mistralai"]
    for sdk in forbidden_in_base:
        assert not any(d.startswith(sdk) for d in declared), (
            f"{sdk!r} must not be a hard dependency — it would force this provider on every user. "
            f"If needed for an optional integration, put it in [project.optional-dependencies]."
        )


# --- Step 2: token-scrubbing logger ---


def _make_capturing_handler() -> tuple[logging.Handler, io.StringIO]:
    """Return a StreamHandler writing to a StringIO buffer."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s %(exc_text)s"))
    return handler, buf


def test_logger_scrubs_token(monkeypatch):
    """Messages containing the bot token must be replaced with [REDACTED]."""
    fake_token = "FAKE_BOT_TOKEN_STEP2_TEST"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", fake_token)

    # Re-import with the env var set so _SECRETS picks it up.
    import agent.logging as alog
    importlib.reload(alog)

    # Set propagate=False before get_logger so the re-scan in get_logger
    # attaches the filter directly to this non-propagating logger.
    logger = logging.getLogger("test.scrub_token")
    logger.propagate = False
    # get_logger re-scans non-propagating loggers and attaches the filter.
    alog.get_logger("test.scrub_token")
    handler, buf = _make_capturing_handler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("token=%s", fake_token)
        output = buf.getvalue()
        assert fake_token not in output, "Bot token must not appear in log output"
        assert "[REDACTED]" in output, "Scrubbed token must appear as [REDACTED]"
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def test_logger_passes_clean_message(monkeypatch):
    """A message with no secret must pass through unmodified."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "CLEAN_TEST_TOKEN_99")

    import agent.logging as alog
    importlib.reload(alog)

    logger = logging.getLogger("test.clean_pass")
    logger.propagate = False
    alog.get_logger("test.clean_pass")
    handler, buf = _make_capturing_handler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("everything is fine")
        output = buf.getvalue()
        assert "everything is fine" in output
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def test_logger_scrubs_api_key(monkeypatch):
    """Values of env vars ending in _API_KEY must be scrubbed."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "DUMMY_TOKEN_APIKEY_TEST")
    monkeypatch.setenv("FOO_API_KEY", "SUPER_SECRET_APIKEY_VALUE")

    import agent.logging as alog
    importlib.reload(alog)

    logger = logging.getLogger("test.api_key")
    logger.propagate = False
    alog.get_logger("test.api_key")
    handler, buf = _make_capturing_handler()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("key=SUPER_SECRET_APIKEY_VALUE")
        output = buf.getvalue()
        assert "SUPER_SECRET_APIKEY_VALUE" not in output
        assert "[REDACTED]" in output
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def test_logger_single_filter_install(monkeypatch):
    """Calling get_logger multiple times must not install the filter more than once."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SINGLE_INSTALL_TOKEN")

    import agent.logging as alog
    importlib.reload(alog)

    alog.get_logger("test.a")
    alog.get_logger("test.b")
    alog.get_logger("test.c")

    from agent.logging import _ScrubFilter
    root = logging.getLogger()
    scrub_count = sum(1 for f in root.filters if isinstance(f, _ScrubFilter))
    assert scrub_count == 1, (
        f"Expected exactly 1 _ScrubFilter on root logger, found {scrub_count}"
    )


def test_logger_scrubs_traceback(monkeypatch):
    """Exc_text (formatted traceback) must not contain the bot token."""
    fake_token = "TRACEBACK_FAKE_TOKEN_123"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", fake_token)

    import agent.logging as alog
    importlib.reload(alog)

    logger = logging.getLogger("test.traceback")
    logger.propagate = False
    alog.get_logger("test.traceback")
    mem_buf = io.StringIO()
    stream_handler = logging.StreamHandler(mem_buf)
    stream_handler.setFormatter(
        logging.Formatter("%(message)s\n%(exc_text)s")
    )
    logger.addHandler(stream_handler)
    logger.setLevel(logging.DEBUG)
    try:
        try:
            raise RuntimeError(
                f"https://api.telegram.org/bot{fake_token}/getMe failed"
            )
        except RuntimeError:
            logger.error("call failed", exc_info=True)
        # Force the handler to flush/format exc_text by logging again.
        output = mem_buf.getvalue()
        assert fake_token not in output, (
            "Bot token must not appear in traceback output"
        )
        assert "[REDACTED]" in output
    finally:
        logger.removeHandler(stream_handler)
        logger.propagate = True


def test_logger_scrubs_stack_info(monkeypatch):
    """stack_info (when logger called with stack_info=True) must also be scrubbed.

    T1 from eng-review explicitly requires scrubbing record.stack_info, but the
    other tests only exercise the message and exc_text paths. Codex P2 fix.
    """
    fake_token = "STACK_INFO_FAKE_TOKEN_999"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", fake_token)

    import agent.logging as alog
    importlib.reload(alog)

    logger = logging.getLogger("test.stack_info")
    logger.propagate = False
    alog.get_logger("test.stack_info")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s\n%(stack_info)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        # stack_info=True attaches a stack trace to the record. The test asserts
        # the trace itself is scrubbed if it ever contains the secret. We force
        # the secret into the captured stack by including it in the log call site
        # variable name visible in the traceback frames is impractical, so we
        # rely on the log message being part of stack_info-adjacent output and
        # specifically test that ANY field the filter touches stays clean.
        # Easier approach: assert the filter mutates record.stack_info when set.
        record = logger.makeRecord(
            "test.stack_info", logging.WARNING, __file__, 0,
            "msg", (), None, "test_func",
            sinfo=f"Stack trace containing token={fake_token}",
        )
        for f in logger.filters:
            f.filter(record)
        assert fake_token not in (record.stack_info or ""), (
            "stack_info must be scrubbed when set"
        )
        assert "[REDACTED]" in (record.stack_info or "")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def test_logger_scrubs_non_propagating_child(monkeypatch):
    """Non-propagating child loggers must also have the scrub filter attached."""
    fake_token = "NON_PROP_TOKEN_456"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", fake_token)

    import agent.logging as alog
    importlib.reload(alog)

    # Create a non-propagating child logger (simulates a noisy third-party lib).
    child = logging.getLogger("third_party_noise_test")
    child.propagate = False
    child.setLevel(logging.DEBUG)

    # Trigger re-scan via get_logger (A1: re-scan on each get_logger call).
    alog.get_logger("test.trigger_rescan")

    child_buf = io.StringIO()
    child_handler = logging.StreamHandler(child_buf)
    child_handler.setFormatter(logging.Formatter("%(message)s"))
    child.addHandler(child_handler)
    try:
        child.warning("token=%s", fake_token)
        output = child_buf.getvalue()
        assert fake_token not in output, (
            "Non-propagating child logger must scrub the token"
        )
        assert "[REDACTED]" in output
    finally:
        child.removeHandler(child_handler)
        child.propagate = True


def test_logger_filter_installed_at_import_time():
    """Filter must be active at module-import time, before any get_logger call.

    Runs in a fresh subprocess: sets TELEGRAM_BOT_TOKEN, imports agent.logging,
    then calls logging.warning directly (no get_logger). Asserts output is scrubbed.
    """
    fake_token = "IMPORT_TIME_TOKEN_789"
    script_lines = [
        "import os, logging, sys",
        f"os.environ['TELEGRAM_BOT_TOKEN'] = '{fake_token}'",
        "import agent.logging",
        "root = logging.getLogger()",
        "root.setLevel(logging.DEBUG)",
        "buf = []",
        "class Cap(logging.Handler):",
        "    def emit(self, r): buf.append(self.format(r))",
        "h = Cap()",
        "h.setFormatter(logging.Formatter('%(message)s'))",
        "root.addHandler(h)",
        f"root.warning('token={fake_token}')",
        "output = '\\n'.join(buf)",
        f"assert '{fake_token}' not in output, f'Token leaked: {{output!r}}'",
        "assert '[REDACTED]' in output, f'No redaction: {{output!r}}'",
        "print('OK')",
    ]
    script = "\n".join(script_lines)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, (
        f"Subprocess failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "OK" in result.stdout
