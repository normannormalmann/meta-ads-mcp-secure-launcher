"""Log hardening for meta-ads-mcp.

The package configures the root logger at DEBUG with an unbounded file handler,
so httpx writes full request URLs (incl. access_token) and the package logs
request parameters (campaign names, budgets). harden_logging() must run BEFORE
the package is imported: once the root logger has a handler, the package's
logging.basicConfig() is a no-op, and the handler level keeps DEBUG out even
though the package sets its own logger to DEBUG.

Secrets are masked twice: in the message (filter) and in the final formatted
line including tracebacks (formatter), by pattern and by the exact values of
the credentials in the environment.
"""
import hashlib
import hmac
import logging
import logging.handlers
import os
import re
from pathlib import Path

MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
PACKAGE_LOGGER = "meta-ads-mcp"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
NOISY_LOGGERS = ("httpx", "httpcore", "urllib3")
SECRET_ENV_KEYS = ("META_ACCESS_TOKEN", "META_APP_SECRET")
MIN_SECRET_LENGTH = 8
MASK = "***"

SECRET_PATTERNS = (
    (re.compile(r"((?:access_token|input_token|client_secret|fb_exchange_token)=)[^&\s'\"]+"), r"\1" + MASK),
    (re.compile(r"(appsecret_proof=)[0-9a-fA-F]+"), r"\1" + MASK),
    (re.compile(r"EAA[A-Za-z0-9]{20,}"), MASK),
)


def _known_secret_values():
    values = [os.environ.get(key, "") for key in SECRET_ENV_KEYS]
    token, app_secret = values
    if token and app_secret:
        values.append(hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest())
    return [value for value in values if len(value) >= MIN_SECRET_LENGTH]


def mask_secrets(text):
    for value in _known_secret_values():
        text = text.replace(value, MASK)
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretMaskingFilter(logging.Filter):
    """Renders the record once and replaces secrets in the message; never raises."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # broken %-format args must not break logging
            message = f"[unformattable log record: {record.msg!r}]"
        record.msg = mask_secrets(message)
        record.args = None
        return True


class SecretMaskingFormatter(logging.Formatter):
    """Masks the final line, including exception tracebacks and stack info."""

    def format(self, record):
        return mask_secrets(super().format(record))


def default_log_path():
    """Same location the package uses on Windows (core/utils.py:setup_logging)."""
    appdata = os.environ.get("APPDATA")
    log_dir = Path(appdata) / "meta-ads-mcp" if appdata else Path.home() / ".meta-ads-mcp"
    return log_dir / "meta_ads_debug.log"


def harden_logging(log_path=None):
    log_path = Path(log_path or default_log_path())
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8", delay=True
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(SecretMaskingFormatter(LOG_FORMAT))
    handler.addFilter(SecretMaskingFilter())

    root = logging.getLogger()
    for existing in root.handlers:
        existing.close()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler


def adopt_package_logger(handler):
    """Points the package logger at our rotating handler; call after the import.

    Since v1.0.128 the package attaches its own unrotated FileHandler to its
    logger (propagate=False). Two open handles on one file break rotation on
    Windows, so the package handler is closed and replaced by ours.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER)
    for existing in package_logger.handlers:
        existing.close()
    package_logger.handlers = [handler]
    package_logger.setLevel(logging.INFO)
