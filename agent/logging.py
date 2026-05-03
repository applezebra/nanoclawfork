"""Token-scrubbing logger.

Installs a filter at module-import time that replaces known secret values with
[REDACTED] in every log record's message, formatted exception text, and stack
info. Use get_logger(name) everywhere — never logging.getLogger directly.
"""
import logging
import os


def _collect_secrets() -> list[str]:
    """Read secret values from env once at import time.

    Sorted longest-first so that if one secret is a prefix of another, the
    longer one is replaced first (codex-review P1: prevents prefix-leak where
    a shorter secret replaces partway through a longer secret, leaving the
    suffix in plaintext).
    """
    secrets = []
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        secrets.append(token)
    for name, value in os.environ.items():
        if name.endswith("_API_KEY") and value and value not in secrets:
            secrets.append(value)
    return sorted(secrets, key=len, reverse=True)


_SECRETS: list[str] = _collect_secrets()


def _scrub_text(text: str) -> str:
    """Replace every known secret in `text` with [REDACTED]."""
    if not _SECRETS:
        return text
    for secret in _SECRETS:
        text = text.replace(secret, "[REDACTED]")
    return text


# Defense in depth — three layers, because Python logging has multiple paths
# from "log call" to "string emitted by a handler":
#
#   Layer 1: LogRecord factory scrubs record.msg + record.args at creation.
#            Any formatter that reads record.getMessage() (the canonical path)
#            sees scrubbed content, including formatter subclasses that override
#            format() without calling super (codex-review L2 P1).
#
#   Layer 2: logging.Formatter.format / formatException patch scrubs the final
#            string output. Catches default Formatter and any subclass that
#            calls super().format(). Also handles exc_text scrubbing.
#
#   Layer 3: _ScrubFilter on root logger backstops anything that reads
#            record.exc_text or record.stack_info directly without going
#            through a Formatter.
#
# Guard against double-install (importlib.reload in tests would otherwise cause
# infinite recursion via _orig_format → _scrubbing_format → _orig_format → ...).

# NOTE on importlib.reload safety: Python closures over module-level names
# resolve by name at call time. If we capture `_orig = stdlib_default` at module
# scope and then reload the module, the module-level name `_orig` rebinds to the
# CURRENTLY installed (already-patched) function, and our patched function
# recurses into itself on next call. Fix: capture the original via a function
# default argument, which is bound at function-definition time and is immune to
# subsequent module reloads. This is why every patch below uses `_orig=...`.

# Layer 1: LogRecord factory.
_existing_factory = logging.getLogRecordFactory()
if not getattr(_existing_factory, "_kayaclaw_scrubbing", False):
    def _scrubbing_record_factory(*args, _orig=_existing_factory, **kwargs):  # type: ignore[no-untyped-def]
        record = _orig(*args, **kwargs)
        if not _SECRETS:
            return record
        # Scrub the format string and each positional/keyword arg. record.getMessage()
        # then returns scrubbed text whether or not the formatter calls super.
        if isinstance(record.msg, str):
            record.msg = _scrub_text(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    _scrub_text(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (_scrub_text(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return record

    _scrubbing_record_factory._kayaclaw_scrubbing = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_scrubbing_record_factory)

# Layer 2: Formatter method patch.
if not getattr(logging.Formatter.format, "_kayaclaw_scrubbing", False):
    def _scrubbing_format(  # type: ignore[no-untyped-def]
        self, record, _orig=logging.Formatter.format
    ):
        return _scrub_text(_orig(self, record))

    def _scrubbing_format_exception(  # type: ignore[no-untyped-def]
        self, exc_info, _orig=logging.Formatter.formatException
    ):
        return _scrub_text(_orig(self, exc_info))

    _scrubbing_format._kayaclaw_scrubbing = True  # type: ignore[attr-defined]
    _scrubbing_format_exception._kayaclaw_scrubbing = True  # type: ignore[attr-defined]
    logging.Formatter.format = _scrubbing_format  # type: ignore[method-assign]
    logging.Formatter.formatException = _scrubbing_format_exception  # type: ignore[method-assign]


class _ScrubFilter(logging.Filter):
    """Defense-in-depth: scrub record fields in place so anyone reading
    record.msg / record.exc_text / record.stack_info directly (not via a
    Formatter) still sees scrubbed content. The Formatter patch above is the
    primary defense; this filter is the backstop.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _SECRETS:
            return True
        record.msg = _scrub_text(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = _scrub_text(record.exc_text)
        if record.stack_info:
            record.stack_info = _scrub_text(record.stack_info)
        return True


def _install_filter() -> None:
    """Attach the scrub filter to root logger and non-propagating child loggers."""
    scrub = _ScrubFilter()
    root = logging.getLogger()
    # Avoid double-install on root.
    if not any(isinstance(f, _ScrubFilter) for f in root.filters):
        root.addFilter(scrub)
    # Also attach to any already-created non-propagating loggers.
    for logger_ref in logging.Logger.manager.loggerDict.values():
        if isinstance(logger_ref, logging.Logger) and not logger_ref.propagate:
            if not any(isinstance(f, _ScrubFilter) for f in logger_ref.filters):
                logger_ref.addFilter(scrub)


# Module-import-time install (eng-review fix A2).
_install_filter()

_HANDLER = logging.StreamHandler()
_HANDLER.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
logging.getLogger().addHandler(_HANDLER)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger; ensure root level and filter are current."""
    root = logging.getLogger()
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))
    # Re-scan for non-propagating loggers created after import (eng-review A1).
    _install_filter()
    return logging.getLogger(name)
