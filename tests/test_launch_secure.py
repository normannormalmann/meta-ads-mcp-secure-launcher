import pytest

import launch_secure
from conftest import FAKE_TOKEN


def test_load_secrets_returns_env_mapping():
    stored = {launch_secure.CRED_APP_SECRET: "secret", launch_secure.CRED_ACCESS_TOKEN: FAKE_TOKEN}

    result = launch_secure.load_secrets(stored.get)

    assert result == {"META_APP_SECRET": "secret", "META_ACCESS_TOKEN": FAKE_TOKEN}


def test_load_secrets_exits_with_message_when_missing(capsys):
    with pytest.raises(SystemExit) as exc:
        launch_secure.load_secrets(lambda target: None)

    assert exc.value.code != 0
    assert "store_secrets.py" in capsys.readouterr().err


def test_load_policy_exits_on_invalid_configuration(capsys):
    with pytest.raises(SystemExit) as exc:
        launch_secure.load_policy({"META_GUARD_MAX_DAILY_BUDGET": "unlimited"})

    assert exc.value.code != 0
    assert "META_GUARD_MAX_DAILY_BUDGET" in capsys.readouterr().err


def test_invalid_default_account_stops_start(capsys):
    with pytest.raises(SystemExit) as exc:
        launch_secure.check_extension_config({"META_DEFAULT_ACCOUNT_ID": "my account"})

    assert exc.value.code != 0
    assert "META_DEFAULT_ACCOUNT_ID" in capsys.readouterr().err


def test_valid_default_account_passes():
    assert launch_secure.check_extension_config({"META_DEFAULT_ACCOUNT_ID": "123"}) == "act_123"


def test_invalid_default_ig_user_stops_start(capsys):
    with pytest.raises(SystemExit):
        launch_secure.check_extension_config({"META_DEFAULT_IG_USER_ID": "@brand"})

    assert "META_DEFAULT_IG_USER_ID" in capsys.readouterr().err


def test_credential_read_errors_stop_with_clean_message(capsys):
    def broken(target):
        raise launch_secure.CredentialReadError("Windows error 5")

    with pytest.raises(SystemExit) as exc:
        launch_secure.load_secrets(broken)

    assert exc.value.code == 1
    assert "Windows error 5" in capsys.readouterr().err


def test_read_secrets_raises_instead_of_exiting():
    with pytest.raises(launch_secure.CredentialsMissing) as exc:
        launch_secure.read_secrets(lambda target: None)

    assert "META_ACCESS_TOKEN" in str(exc.value)


def test_unsupported_upstream_stops_with_hint(monkeypatch, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "meta_ads_mcp", None)  # import raises ImportError

    with pytest.raises(SystemExit) as exc:
        launch_secure.wire_upstream(log_handler=None, environ={})

    assert exc.value.code == 1
    assert "meta-ads-mcp" in capsys.readouterr().err
