import pytest

import check_token

NOW = 1_800_000_000
DAY = 86_400


def debug_data(**overrides):
    data = {
        "is_valid": True,
        "type": "SYSTEM_USER",
        "application": "Example Ads App",
        "expires_at": NOW + 50 * DAY,
        "data_access_expires_at": NOW + 80 * DAY,
        "scopes": ["ads_management", "ads_read"],
    }
    return {**data, **overrides}


def test_healthy_token_has_no_warnings():
    report = check_token.assess(debug_data(), NOW, "de")

    assert report.warnings == []
    assert any("50 Tagen" in line for line in report.lines)


def test_report_is_available_in_english():
    report = check_token.assess(debug_data(expires_at=0), NOW, "en")

    assert any("never expires" in line for line in report.lines)


def test_never_expiring_token_is_reported():
    report = check_token.assess(debug_data(expires_at=0), NOW, "de")

    assert any("läuft nie ab" in line for line in report.lines)


def test_warns_when_expiring_soon():
    report = check_token.assess(debug_data(expires_at=NOW + 5 * DAY), NOW, "de")

    assert any("5 Tagen" in warning for warning in report.warnings)


def test_warns_when_data_access_expiring_soon():
    report = check_token.assess(debug_data(data_access_expires_at=NOW + 3 * DAY), NOW, "de")

    assert any("Datenzugriff" in warning for warning in report.warnings)


def test_warns_on_invalid_token():
    report = check_token.assess(debug_data(is_valid=False), NOW, "de")

    assert any("ungültig" in warning for warning in report.warnings)


def test_accepts_page_scopes_needed_for_creatives():
    scopes = ["ads_management", "ads_read", "pages_show_list", "pages_read_engagement", "pages_manage_ads", "instagram_basic"]

    assert check_token.assess(debug_data(scopes=scopes), NOW, "en").warnings == []


def test_warns_on_unneeded_scopes():
    report = check_token.assess(debug_data(scopes=["ads_management", "ads_read", "pages_manage_posts", "email"]), NOW, "en")

    [warning] = report.warnings
    assert "pages_manage_posts" in warning
    assert "email" in warning


def test_report_never_contains_token():
    report = check_token.assess(debug_data(), NOW, "en")

    assert all("EAA" not in line for line in report.lines + report.warnings)


def test_resolve_app_id_prefers_argument_then_env():
    assert check_token.resolve_app_id("111111", {"META_APP_ID": "222222"}) == "111111"
    assert check_token.resolve_app_id(None, {"META_APP_ID": "222222"}) == "222222"


def test_resolve_app_id_missing_returns_none():
    assert check_token.resolve_app_id(None, {}) is None


def test_main_without_app_id_fails_with_message(capsys, monkeypatch):
    monkeypatch.delenv("META_APP_ID", raising=False)

    assert check_token.main(["--lang", "en"]) == 2
    assert "META_APP_ID" in capsys.readouterr().err


@pytest.mark.parametrize("lang", ["de", "en"])
def test_main_prints_report_without_secrets(capsys, monkeypatch, lang):
    secrets = {"META_APP_SECRET": "s" * 32, "META_ACCESS_TOKEN": "EAA" + "z" * 100}
    monkeypatch.setattr(check_token, "load_secrets", lambda: secrets)
    monkeypatch.setattr(check_token, "fetch_debug_data", lambda app_id, loaded: debug_data(expires_at=0))

    exit_code = check_token.main(["--app-id", "123456789", "--lang", lang])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert secrets["META_ACCESS_TOKEN"] not in output
    assert secrets["META_APP_SECRET"] not in output


def test_instagram_insights_scope_needed_only_when_enabled():
    scopes = ["ads_management", "ads_read", "instagram_basic", "instagram_manage_insights"]

    default = check_token.assess(debug_data(scopes=scopes), NOW, "en")
    enabled = check_token.assess(debug_data(scopes=scopes), NOW, "en", check_token.expected_scopes({"META_ENABLE_INSTAGRAM_INSIGHTS": "1"}))

    assert "instagram_manage_insights" in default.warnings[0]
    assert enabled.warnings == []
