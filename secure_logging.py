"""Log hardening for meta-ads-mcp.

The package configures the root logger at DEBUG with an unbounded file handler,
so httpx writes full request URLs (incl. access_token) and the package logs
request parameters (campaign names, budgets). harden_logging() must run BEFORE
the package is imported: once the root logger has a handler, the package's
logging.basicConfig() is a no-op, and the handler level keeps DEBUG out even
though the package sets its own logger to DEBUG.
"""
import logging
import logging.handlers
import os
import re
from pathlib import Path

MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
PACKAGE_LOGGER = "meta-ads-mcp"
LOG_FORMAT ="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
NOISY_LOGGERS = ("httpx", "httpcore", "urllib3")

SECRET_PATTERNS = (
    (re.compile(r"(access_token=)[^&\s'\"]+"), r"\1***"),
    (re.compile(r"(appsecret_proof=)[0-9a-fA-F]+"), r"\1***"),
    (re.compile(r"EAA[A-Za-z0-9]{20,}"), "***"),
)


def mask_secrets(text):
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretMaskingFilter(logging.Filter):
    """Renders the record once and replaces secrets in the final message."""

    def filter(self, record):
        record.msg = mask_secrets(record.getMessage())
        record.args = None
        return True


def default_log_path():
    """Same location the package uses on Windows (core/utils.py:setup_logging)."""
    return Path(os.environ["APPDATA"]) / "meta-ads-mcp" / "meta_ads_debug.log"


def harden_logging(log_path=None):
    log_path = Path(log_path or default_log_path())
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
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
