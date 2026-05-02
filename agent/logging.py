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


class _ScrubFilter(logging.Filter):
    """Replace known secret values with [REDACTED] across all record fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _SECRETS:
            return True
        # Scrub the fully-substituted message so secrets in %-format args are caught.
        record.msg = self._scrub(record.getMessage())
        record.args = None
        # exc_text is formatted lazily by the handler; pre-format and scrub it
        # here so the cached value is already clean when the handler reads it.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)
        return True

    @staticmethod
    def _scrub(text: str) -> str:
        for secret in _SECRETS:
            text = text.replace(secret, "[REDACTED]")
        return text


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
