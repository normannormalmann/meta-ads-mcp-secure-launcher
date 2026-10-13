import pytest

import store_secrets
from launch_secure import CRED_ACCESS_TOKEN, CRED_APP_SECRET


def test_accepts_valid_app_secret():
    assert store_secrets.validate(CRED_APP_SECRET, "0123456789abcdef" * 2, "en") is None


def test_rejects_app_secret_with_wrong_length():
    assert "32" in store_secrets.validate(CRED_APP_SECRET, "abc123", "en")


def test_accepts_valid_token():
    assert store_secrets.validate(CRED_ACCESS_TOKEN, "EAA" + "x" * 200, "en") is None


def test_rejects_token_without_eaa_prefix():
    assert "EAA" in store_secrets.validate(CRED_ACCESS_TOKEN, "abc" + "x" * 200, "de")


def test_rejects_token_with_whitespace():
    assert store_secrets.validate(CRED_ACCESS_TOKEN, "EAA" + "x" * 100 + " " + "y" * 100, "en") is not None


@pytest.mark.parametrize("lang, expected", [("de", "207 Zeichen"), ("en", "207 characters")])
def test_feedback_shows_length_and_tail_only(lang, expected):
    secret = "EAA" + "x" * 200 + "WXYZ"

    feedback = store_secrets.describe(secret, lang)

    assert expected in feedback
    assert "WXYZ" in feedback
    assert "EAAxxx" not in feedback


def test_prompt_secret_retries_until_valid(monkeypatch, capsys):
    answers = iter(["", "too-short", "0123456789abcdef" * 2])
    monkeypatch.setattr(store_secrets.getpass, "getpass", lambda prompt: next(answers))

    assert store_secrets.prompt_secret(CRED_APP_SECRET, "en") == "0123456789abcdef" * 2
    assert "Nothing received" in capsys.readouterr().out


def test_status_reports_presence_without_values(capsys):
    stored = {CRED_APP_SECRET: "secret-value", CRED_ACCESS_TOKEN: None}

    exit_code = store_secrets.print_status(stored.get, "en")
    output = capsys.readouterr().out

    assert exit_code == 1
    assert f"{CRED_APP_SECRET}: stored" in output
    assert f"{CRED_ACCESS_TOKEN}: missing" in output
    assert "secret-value" not in output
