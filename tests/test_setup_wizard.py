import json
from datetime import datetime
from pathlib import Path

import pytest

import setup_wizard
from tools.wizard_fakes import ScriptedConsole, FakeSystem

APP_ID = "123456789012345"
NOW = datetime(2026, 10, 13, 12, 0, 0)
LEGACY_ENTRY = {"command": "meta-ads-mcp.exe", "env": {"META_APP_ID": APP_ID, "META_ACCESS_TOKEN": "EAA-placeholder"}}
OTHER_SERVER = {"command": "npx", "args": ["other-mcp"]}


@pytest.fixture
def root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def make_wizard(root, answers, config_path=None, lang="en", **system_options):
    console = ScriptedConsole(answers)
    existing = {root / "requirements.lock", *system_options.pop("existing", ())}
    system = FakeSystem(console, existing=existing, **system_options)
    wizard = setup_wizard.Wizard(console, system, lang, config_path, root=root, now=lambda: NOW)
    return wizard, console, system


def write_config(path, config):
    path.write_text(json.dumps(config), encoding="utf-8")


def happy_answers(legacy=True):
    # app id, default account, instagram?, store credentials?, daily, lifetime, domains, [remove legacy?], write config?
    return [APP_ID, "", "", "y", "", "", ""] + ([""] if legacy else []) + [""]


def test_happy_path_writes_entry_keeps_other_servers_and_removes_legacy(root, tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    write_config(config_path, {"mcpServers": {"meta-ads-mcp": LEGACY_ENTRY, "other": OTHER_SERVER}, "preferences": {"x": 1}})
    wizard, console, system = make_wizard(root, happy_answers(), config_path, results={"store_secrets --status": 1})

    assert wizard.run() == 0

    config = json.loads(config_path.read_text(encoding="utf-8"))
    entry = config["mcpServers"]["meta-ads"]
    assert set(config["mcpServers"]) == {"meta-ads", "other"}
    assert config["preferences"] == {"x": 1}
    assert entry["args"] == [str(root / "launch_secure.py")]
    assert entry["env"] == {"META_APP_ID": APP_ID, "META_GUARD_MAX_DAILY_BUDGET": "250", "META_GUARD_MAX_LIFETIME_BUDGET": "5000"}
    backup = config_path.with_name("claude_desktop_config.json.bak-20261013-120000")
    backed_up = json.loads(backup.read_text(encoding="utf-8"))["mcpServers"]
    assert backed_up["meta-ads-mcp"]["env"]["META_ACCESS_TOKEN"] == setup_wizard.REDACTED
    assert backed_up["meta-ads-mcp"]["env"]["META_APP_ID"] == APP_ID
    assert backed_up["other"] == OTHER_SERVER
    assert "EAA-placeholder" not in backup.read_text(encoding="utf-8")
    assert "rotate" in console.text()
    assert list(config_path.parent.glob("*.tmp*")) == []
    assert [key for key, _, _ in system.calls] == ["venv", "pip", "pytest", "store_secrets --status", "store_secrets", "check_token"]


def test_entry_never_contains_secrets(root):
    entry = setup_wizard.build_server_entry(Path("python.exe"), root / "launch_secure.py", APP_ID, 100, 1000)

    assert not set(entry["env"]) & set(setup_wizard.SECRET_ENV_KEYS)


def test_check_token_receives_app_id_and_language(root, tmp_path):
    wizard, _, system = make_wizard(root, happy_answers(legacy=False), tmp_path / "c.json", lang="de", results={"store_secrets --status": 1})

    wizard.run()

    [(_, args, _)] = [call for call in system.calls if call[0] == "check_token"]
    assert args[-4:] == ["--app-id", APP_ID, "--lang", "de"]


def test_custom_limits_are_written(root, tmp_path):
    config_path = tmp_path / "c.json"
    wizard, _, _ = make_wizard(root, [APP_ID, "", "", "n", "100", "2000", "", ""], config_path, results={"store_secrets --status": 1})

    wizard.run()

    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["meta-ads"]["env"]
    assert env["META_GUARD_MAX_DAILY_BUDGET"] == "100"
    assert env["META_GUARD_MAX_LIFETIME_BUDGET"] == "2000"


def test_invalid_inputs_are_asked_again(root, tmp_path):
    answers = ["abc", APP_ID, "", "", "n", "-5", "0", "100", "", "", "maybe", "y"]
    wizard, console, _ = make_wizard(root, answers, tmp_path / "c.json", results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert "5-20 digits" in console.text()
    assert "positive whole number" in console.text()
    assert "answer with y or n" in console.text()


def test_existing_credentials_are_kept_by_default(root, tmp_path):
    wizard, _, system = make_wizard(root, [APP_ID, "", "", "", "", "", "", ""], tmp_path / "c.json", results={"store_secrets --status": 0})

    wizard.run()

    assert "store_secrets" not in [key for key, _, _ in system.calls]


def test_existing_venv_can_skip_reinstall(root, tmp_path):
    venv_python = root / "venv" / "Scripts" / "python.exe"
    wizard, _, system = make_wizard(root, ["n"] + happy_answers(legacy=False), tmp_path / "c.json", existing={venv_python}, results={"store_secrets --status": 1})

    wizard.run()

    assert [key for key, _, _ in system.calls][:1] == ["pytest"]


@pytest.mark.parametrize("options, message", [
    ({"platform_name": "Linux"}, "only supports Windows and macOS"),
    ({"python": (3, 10, 14)}, "3.11 or newer"),
])
def test_prerequisite_failures_abort(root, options, message):
    wizard, console, system = make_wizard(root, [], **options)

    assert wizard.run() == 1
    assert message in console.text()
    assert system.calls == []


def test_install_failure_aborts(root):
    wizard, console, _ = make_wizard(root, [], results={"pip": 1})

    assert wizard.run() == 1
    assert "Installation failed" in console.text()


def test_invalid_json_config_is_left_untouched(root, tmp_path):
    config_path = tmp_path / "c.json"
    config_path.write_text("{ broken", encoding="utf-8")
    wizard, console, _ = make_wizard(root, [APP_ID, "", "", "n", "", "", ""], config_path, results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert config_path.read_text(encoding="utf-8") == "{ broken"
    assert "not valid JSON" in console.text()


def test_declining_write_leaves_config_unchanged(root, tmp_path):
    config_path = tmp_path / "c.json"
    write_config(config_path, {"mcpServers": {"other": OTHER_SERVER}})
    original = config_path.read_text(encoding="utf-8")
    wizard, _, _ = make_wizard(root, [APP_ID, "", "", "n", "", "", "", "n"], config_path, results={"store_secrets --status": 1})

    wizard.run()

    assert config_path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.bak-*")) == []


def test_missing_config_prints_manual_snippet(root):
    wizard, console, _ = make_wizard(root, [APP_ID, "", "", "n", "", "", ""], None, results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert '"mcpServers"' in console.text()


def test_eof_aborts_cleanly(root):
    wizard, console, _ = make_wizard(root, [])

    assert wizard.run() == setup_wizard.EXIT_ABORTED
    assert "aborted" in console.text()


def test_german_wizard_runs(root, tmp_path):
    wizard, console, _ = make_wizard(root, [APP_ID, "", "", "j", "", "", "", ""], tmp_path / "c.json", lang="de", results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert "Schritt 1/7" in console.text()


def test_find_claude_config_prefers_existing_store_install(tmp_path):
    store_config = tmp_path / "Local" / "Packages" / "Claude_abc" / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
    store_config.parent.mkdir(parents=True)
    store_config.write_text("{}", encoding="utf-8")
    environ = {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}

    assert setup_wizard.find_claude_config(environ, "Windows") == store_config


def test_find_claude_config_defaults_to_appdata(tmp_path):
    environ = {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}

    assert setup_wizard.find_claude_config(environ, "Windows") == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"


def test_allowed_domains_are_normalized_and_written(root, tmp_path):
    config_path = tmp_path / "c.json"
    wizard, _, _ = make_wizard(root, [APP_ID, "", "", "n", "", "", "Shop.Example.org, example.com", ""], config_path, results={"store_secrets --status": 1})

    wizard.run()

    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["meta-ads"]["env"]
    assert env["META_GUARD_ALLOWED_URL_DOMAINS"] == "example.com,shop.example.org"


def test_invalid_domains_are_asked_again(root, tmp_path):
    config_path = tmp_path / "c.json"
    wizard, console, _ = make_wizard(root, [APP_ID, "", "", "n", "", "", "https://example.com", "example.com", ""], config_path, results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert "domains like example.com" in console.text()


def test_no_domain_entry_without_answer(root):
    entry = setup_wizard.build_server_entry(Path("python.exe"), root / "launch_secure.py", APP_ID, 100, 1000)

    assert "META_GUARD_ALLOWED_URL_DOMAINS" not in entry["env"]


def test_default_account_is_normalized_and_written(root, tmp_path):
    config_path = tmp_path / "c.json"
    wizard, _, _ = make_wizard(root, [APP_ID, "act-x", "987654321", "", "n", "", "", "", ""], config_path, results={"store_secrets --status": 1})

    wizard.run()

    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["meta-ads"]["env"]
    assert env["META_DEFAULT_ACCOUNT_ID"] == "act_987654321"


def test_no_default_account_entry_without_answer(root):
    entry = setup_wizard.build_server_entry(Path("python.exe"), root / "launch_secure.py", APP_ID, 100, 1000)

    assert "META_DEFAULT_ACCOUNT_ID" not in entry["env"]


def test_instagram_option_is_written_and_passed_to_token_check(root, tmp_path):
    config_path = tmp_path / "c.json"
    answers = [APP_ID, "", "y", "@brand", "17841400000000001", "n", "", "", "", ""]
    wizard, console, system = make_wizard(root, answers, config_path, results={"store_secrets --status": 1})

    assert wizard.run() == 0

    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["meta-ads"]["env"]
    assert env["META_ENABLE_INSTAGRAM_INSIGHTS"] == "1"
    assert env["META_DEFAULT_IG_USER_ID"] == "17841400000000001"
    assert "numeric" in console.text()
    [(_, _, token_env)] = [call for call in system.calls if call[0] == "check_token"]
    assert token_env["META_ENABLE_INSTAGRAM_INSIGHTS"] == "1"


def test_instagram_is_off_by_default(root, tmp_path):
    config_path = tmp_path / "c.json"
    wizard, _, system = make_wizard(root, happy_answers(legacy=False), config_path, results={"store_secrets --status": 1})

    wizard.run()

    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["meta-ads"]["env"]
    assert "META_ENABLE_INSTAGRAM_INSIGHTS" not in env
    [(_, _, token_env)] = [call for call in system.calls if call[0] == "check_token"]
    assert token_env is None


@pytest.mark.parametrize("content", ["[]", '{"mcpServers": []}', '{"mcpServers": {"x": "not a server"}}'])
def test_unexpected_config_structure_is_left_untouched(root, tmp_path, content):
    config_path = tmp_path / "c.json"
    config_path.write_text(content, encoding="utf-8")
    wizard, console, _ = make_wizard(root, [APP_ID, "", "", "n", "", "", ""], config_path, results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert config_path.read_text(encoding="utf-8") == content
    assert "configuration" in console.text()


def test_unicode_digits_are_asked_again_instead_of_crashing(root, tmp_path):
    answers = [APP_ID, "", "", "n", "²", "100", "", "", ""]
    wizard, console, _ = make_wizard(root, answers, tmp_path / "c.json", results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert "positive whole number" in console.text()


def test_macos_uses_venv_bin_python_and_keychain_wording(root, tmp_path):
    config_path = tmp_path / "claude_desktop_config.json"
    wizard, console, system = make_wizard(root, happy_answers(legacy=False), config_path, platform_name="Darwin",
                                          results={"store_secrets --status": 1})

    assert wizard.run() == 0

    entry = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["meta-ads"]
    assert entry["command"] == str(root / "venv" / "bin" / "python")
    assert all(args[0] == str(root / "venv" / "bin" / "python") for key, args, _ in system.calls if key != "venv")
    text = console.text()
    assert "macOS detected" in text and "macOS Keychain" in text
    assert "Cmd+Q" in text and "venv/bin/python check_token.py" in text
    assert "Scripts" not in text and "tray icon" not in text


def test_german_macos_wording(root, tmp_path):
    wizard, console, _ = make_wizard(root, [APP_ID, "", "", "j", "", "", "", ""], tmp_path / "c.json", lang="de",
                                     platform_name="Darwin", results={"store_secrets --status": 1})

    assert wizard.run() == 0
    assert "macOS-Schlüsselbund" in console.text()


def test_find_claude_config_on_macos(tmp_path):
    environ = {"HOME": str(tmp_path)}

    assert setup_wizard.find_claude_config(environ, "Darwin") == (
        tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")


def test_venv_python_hint_matches_platform():
    assert setup_wizard.venv_python_hint("Windows") == "venv\\Scripts\\python.exe"
    assert setup_wizard.venv_python_hint("Darwin") == "venv/bin/python"
