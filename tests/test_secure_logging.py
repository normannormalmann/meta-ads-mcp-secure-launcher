import logging
import logging.handlers

import pytest

import secure_logging
from conftest import FAKE_PROOF, FAKE_TOKEN


def make_record(msg, args=None):
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, None)


def masked_message(msg, args=None):
    record = make_record(msg, args)
    assert secure_logging.SecretMaskingFilter().filter(record) is True
    return record.getMessage()


def test_masks_access_token_and_proof_in_url():
    url = f"https://graph.facebook.com/v24.0/me?access_token={FAKE_TOKEN}&appsecret_proof={FAKE_PROOF}&limit=5"

    result = masked_message(url)

    assert FAKE_TOKEN not in result
    assert FAKE_PROOF not in result
    assert "access_token=***" in result
    assert "appsecret_proof=***" in result
    assert "limit=5" in result


def test_masks_secrets_passed_via_record_args():
    result = masked_message('HTTP Request: %s %s "%s"', ("GET", f"https://x?access_token={FAKE_TOKEN}", "200 OK"))

    assert FAKE_TOKEN not in result
    assert "200 OK" in result


def test_masks_bare_meta_token():
    assert masked_message(f"token was {FAKE_TOKEN} end") == "token was *** end"


def test_leaves_harmless_message_unchanged():
    assert masked_message("Fetched %d campaigns", (12,)) == "Fetched 12 campaigns"


@pytest.fixture
def isolated_root_logger(tmp_path):
    root = logging.getLogger()
    package_logger = logging.getLogger("meta-ads-mcp")
    saved = root.handlers[:], root.level, package_logger.handlers[:], package_logger.level, package_logger.propagate
    root.handlers = []
    package_logger.handlers = []
    yield tmp_path / "meta_ads_debug.log"
    for handler in root.handlers + package_logger.handlers:
        handler.close()
    root.handlers, level, package_logger.handlers, package_level, package_logger.propagate = saved
    root.setLevel(level)
    package_logger.setLevel(package_level)


def file_handlers():
    return [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]


def test_harden_logging_rotates_masks_and_reduces_level(isolated_root_logger):
    secure_logging.harden_logging(isolated_root_logger)
    # the package sets its own logger to DEBUG after our setup; the handler must still filter
    logging.getLogger("meta-ads-mcp").setLevel(logging.DEBUG)

    logging.getLogger("meta-ads-mcp").debug("Request params with campaign budget")
    logging.getLogger("httpx").info(f"GET https://x?access_token={FAKE_TOKEN}")
    logging.getLogger("httpx").warning(f"retry https://x?access_token={FAKE_TOKEN}")
    logging.getLogger("meta-ads-mcp").info("Server gestartet – Umlaute äöü")
    [handler] = file_handlers()
    handler.flush()
    content = isolated_root_logger.read_text(encoding="utf-8")

    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == secure_logging.MAX_LOG_BYTES
    assert handler.backupCount == secure_logging.LOG_BACKUP_COUNT
    assert "Request params" not in content
    assert "GET https://x" not in content
    assert "retry https://x?access_token=***" in content
    assert FAKE_TOKEN not in content
    assert "Server gestartet – Umlaute äöü" in content


def simulate_package_setup_logging(log_file):
    """Mirrors meta_ads_mcp/core/utils.py:setup_logging() of v1.0.128."""
    package_logger = logging.getLogger("meta-ads-mcp")
    package_logger.setLevel(logging.DEBUG)
    own_handler = logging.FileHandler(str(log_file))
    own_handler._meta_ads_mcp_handler = True
    package_logger.addHandler(own_handler)
    package_logger.propagate = False
    return package_logger, own_handler


def test_adopt_package_logger_shares_single_rotating_handler(isolated_root_logger):
    handler = secure_logging.harden_logging(isolated_root_logger)
    package_logger, own_handler = simulate_package_setup_logging(isolated_root_logger)

    secure_logging.adopt_package_logger(handler)
    package_logger.debug("Request params with campaign budget")
    package_logger.info(f"token {FAKE_TOKEN} – äöü")
    logging.getLogger("meta-ads-mcp.write-guard").warning("Blocked POST")
    handler.flush()
    content = isolated_root_logger.read_text(encoding="utf-8")

    assert package_logger.handlers == [handler]
    assert own_handler.stream is None  # closed, so rotation can rename the file
    assert "Request params" not in content
    assert FAKE_TOKEN not in content
    assert "token *** – äöü" in content
    assert "Blocked POST" in content


def test_package_basic_config_becomes_no_op(isolated_root_logger, tmp_path):
    secure_logging.harden_logging(isolated_root_logger)

    logging.basicConfig(level=logging.DEBUG, filename=str(tmp_path / "other.log"))

    assert len(file_handlers()) == 1
    assert not (tmp_path / "other.log").exists()


def test_tracebacks_are_masked(isolated_root_logger):
    import httpx

    handler = secure_logging.harden_logging(isolated_root_logger)
    request = httpx.Request("GET", f"https://graph.facebook.com/v24.0/1?access_token={FAKE_TOKEN}&appsecret_proof={FAKE_PROOF}")
    try:
        httpx.Response(400, request=request).raise_for_status()
    except httpx.HTTPStatusError:
        logging.getLogger("meta-ads-mcp").exception("create_ad_creative failed")
    handler.flush()
    content = isolated_root_logger.read_text(encoding="utf-8")

    assert "create_ad_creative failed" in content
    assert "Traceback" in content
    assert FAKE_TOKEN not in content
    assert FAKE_PROOF not in content


def test_known_secret_values_are_masked_exactly(monkeypatch):
    secret = "0123456789abcdef" * 2
    monkeypatch.setenv("META_APP_SECRET", secret)
    monkeypatch.setenv("META_ACCESS_TOKEN", "short-token-value-123")

    masked = secure_logging.mask_secrets(f"secret {secret} token short-token-value-123")

    assert secret not in masked
    assert "short-token-value-123" not in masked


@pytest.mark.parametrize("key", ["input_token", "client_secret", "fb_exchange_token"])
def test_additional_secret_parameters_are_masked(key):
    assert secure_logging.mask_secrets(f"GET /x?{key}=abc123secret&y=1") == f"GET /x?{key}=***&y=1"


def test_filter_never_raises_on_broken_records():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%d items", ("not-a-number",), None)

    assert secure_logging.SecretMaskingFilter().filter(record) is True
    assert "unformattable" in record.getMessage()


def test_missing_appdata_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(secure_logging.Path, "home", lambda: tmp_path)

    assert secure_logging.default_log_path() == tmp_path / ".meta-ads-mcp" / "meta_ads_debug.log"
