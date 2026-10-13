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
