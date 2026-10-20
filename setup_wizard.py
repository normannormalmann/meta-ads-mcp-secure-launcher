"""Interactive setup wizard for the meta-ads-mcp secure launcher (Windows and macOS).

Run with Python 3.11-3.14:  python setup_wizard.py (macOS: python3 setup_wizard.py) [--lang de|en] [--claude-config PATH]

Uses only the standard library, because it creates the virtual environment
that holds the actual dependencies. Every change asks for confirmation first.
"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import settings
import url_policy
from i18n import LANGUAGES, add_language_argument, detect_language, platform_text, t

ROOT = Path(__file__).resolve().parent
SERVER_NAME = "meta-ads"
MIN_PYTHON = (3, 11)  # starlette in requirements.lock needs 3.11+
MAX_PYTHON = (3, 14)  # newest version with prebuilt packages for everything in requirements.lock
TOTAL_STEPS = 7
DEFAULT_DAILY_LIMIT = 250
DEFAULT_LIFETIME_LIMIT = 5000
APP_ID_LENGTH = (5, 20)
SECRET_ENV_KEYS = ("META_ACCESS_TOKEN", "META_APP_SECRET")
REDACTED = "***removed-by-setup-wizard***"
DIGITS = re.compile(r"[0-9]+", re.ASCII)
WRAP_WIDTH = 88
EXIT_OK, EXIT_FAILED, EXIT_ABORTED = 0, 1, 130
WINDOWS, MACOS = "Windows", "Darwin"  # platform.system()
SUPPORTED_PLATFORMS = {WINDOWS: "Windows", MACOS: "macOS"}
VENV_PYTHON = {WINDOWS: ("Scripts", "python.exe"), MACOS: ("bin", "python")}


def venv_python(root, system_name=WINDOWS):
    return root.joinpath("venv", *VENV_PYTHON.get(system_name, VENV_PYTHON[MACOS]))


def venv_python_hint(system_name):
    """Relative interpreter path for messages, e.g. venv\\Scripts\\python.exe or venv/bin/python."""
    separator = "\\" if system_name == WINDOWS else "/"
    return separator.join(("venv", *VENV_PYTHON.get(system_name, VENV_PYTHON[MACOS])))


def find_claude_config(environ, system_name):
    """Microsoft Store installs keep the config in a package folder; prefer an existing one."""
    if system_name == MACOS:
        home = environ.get("HOME")
        return Path(home, "Library", "Application Support", "Claude", "claude_desktop_config.json") if home else None
    local, roaming = environ.get("LOCALAPPDATA"), environ.get("APPDATA")
    if local:
        pattern = "Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"
        for candidate in sorted(Path(local, "Packages").glob(pattern)):
            return candidate
    return Path(roaming, "Claude", "claude_desktop_config.json") if roaming else None


def build_server_entry(python_exe, launcher, app_id, daily_limit, lifetime_limit, allowed_domains="", default_account=None,
                       instagram=False, default_ig_user=None):
    env = {
        "META_APP_ID": app_id,
        **({settings.ENV_DEFAULT_ACCOUNT: default_account} if default_account else {}),
        **({settings.ENV_ENABLE_INSTAGRAM: "1"} if instagram else {}),
        **({settings.ENV_DEFAULT_IG_USER: default_ig_user} if instagram and default_ig_user else {}),
        "META_GUARD_MAX_DAILY_BUDGET": str(daily_limit),
        "META_GUARD_MAX_LIFETIME_BUDGET": str(lifetime_limit),
    }
    if allowed_domains:
        env[url_policy.ENV_ALLOWED_DOMAINS] = allowed_domains
    return {"command": str(python_exe), "args": [str(launcher)], "env": env}


def is_valid_account_id(value):
    try:
        settings.parse_account_id(value)
    except ValueError:
        return False
    return True


def is_valid_ig_user_id(value):
    try:
        settings.parse_ig_user_id(value)
    except ValueError:
        return False
    return True


def is_valid_domain_list(value):
    try:
        url_policy.parse_domains(value)
    except ValueError:
        return False
    return True


def is_valid_config(config):
    """A Claude Desktop config is an object whose mcpServers (if present) map names to objects."""
    if not isinstance(config, dict):
        return False
    servers = config.get("mcpServers", {})
    return isinstance(servers, dict) and all(
        isinstance(server, dict) and isinstance(server.get("env", {}), dict) for server in servers.values()
    )


def _has_secrets(server):
    return any(key in server.get("env", {}) for key in SECRET_ENV_KEYS)


def contains_plaintext_secrets(config):
    return any(_has_secrets(server) for server in config.get("mcpServers", {}).values())


def redacted_config(config):
    """Copy of the config with Meta credentials replaced; other entries stay as they are."""
    def redact(server):
        env = {key: REDACTED if key in SECRET_ENV_KEYS else value for key, value in server.get("env", {}).items()}
        return {**server, "env": env} if "env" in server else server

    servers = {name: redact(server) for name, server in config.get("mcpServers", {}).items()}
    return {**config, "mcpServers": servers} if "mcpServers" in config else config


def _write_atomic(path, text):
    temporary = path.with_name(f"{path.name}.tmp-wizard")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def legacy_entries(config):
    """Other server entries that carry Meta credentials in plain text."""
    return [name for name, server in config.get("mcpServers", {}).items() if name != SERVER_NAME and _has_secrets(server)]


def updated_config(config, entry, remove=()):
    servers = {name: server for name, server in config.get("mcpServers", {}).items() if name not in remove}
    return {**config, "mcpServers": {**servers, SERVER_NAME: entry}}


def save_config(path, old_config, new_config, timestamp):
    """Backs up the old config without Meta credentials, then replaces the file atomically.

    Returns the backup path, or None if there was no previous file.
    """
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{timestamp:%Y%m%d-%H%M%S}")
        _write_atomic(backup, json.dumps(redacted_config(old_config), indent=2, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(new_config, indent=2, ensure_ascii=False))
    return backup


def is_positive_number(value):
    return bool(DIGITS.fullmatch(value)) and int(value) > 0


class TerminalConsole:
    def say(self, text=""):
        print(text, flush=True)

    def ask(self, prompt):
        return input(prompt)


class LocalSystem:
    def __init__(self, cwd):
        self.cwd = cwd

    def platform(self):
        return platform.system()

    def python_version(self):
        return tuple(sys.version_info[:3])

    def python_executable(self):
        return sys.executable

    def exists(self, path):
        return Path(path).exists()

    def run(self, args, env=None):
        sys.stdout.flush()  # keep our output ordered before the child process writes
        return subprocess.call([str(arg) for arg in args], cwd=self.cwd, env=env)


class Wizard:
    def __init__(self, console, system, lang, config_path, root=ROOT, now=datetime.now):
        self.console, self.system, self.lang = console, system, lang
        self.config_path, self.root, self.now = config_path, root, now
        self.system_name = system.platform()
        self.python = venv_python(root, self.system_name)
        self.python_hint = venv_python_hint(self.system_name)
        self.app_id = None
        self.default_account = None
        self.instagram = False
        self.default_ig_user = None
        self.limits = (DEFAULT_DAILY_LIMIT, DEFAULT_LIFETIME_LIMIT)
        self.allowed_domains = ""

    # --- output helpers -------------------------------------------------
    def _t(self, key, **values):
        return t(self.lang, key, **values)

    def _platform_text(self, prefix):
        return platform_text(self.lang, prefix, self.system_name)

    @staticmethod
    def _wrap(text, prefix):
        return textwrap.fill(text, WRAP_WIDTH, initial_indent=prefix, subsequent_indent=" " * len(prefix),
                             break_long_words=False, break_on_hyphens=False)

    def _say(self, key, prefix="", **values):
        text = self._t(key, **values)
        self.console.say(self._wrap(text, prefix) if prefix else text)

    def _paragraph(self, key, **values):
        self.console.say(textwrap.fill(self._t(key, **values), WRAP_WIDTH))

    def _ok(self, key, **values):
        self._say(key, "[OK] ", **values)

    def _warn(self, key, **values):
        self._say(key, "[!]  ", **values)

    def _error(self, key, **values):
        self._say(key, "[x]  ", **values)

    def _heading(self, number, key):
        self.console.say("")
        self.console.say(self._t("step", current=number, total=TOTAL_STEPS, title=self._t(key)))
        self.console.say("-" * WRAP_WIDTH)

    def _json(self, value):
        self.console.say(json.dumps(value, indent=2, ensure_ascii=False))

    # --- input helpers --------------------------------------------------
    def _confirm(self, key, default=True, **values):
        hint = self._t("yes_no_default_yes" if default else "yes_no_default_no")
        yes, no = (set(self._t(name).split(",")) for name in ("yes_answers", "no_answers"))
        while True:
            answer = self.console.ask(self._wrap(f"{self._t(key, **values)} {hint}", "[?]  ") + " ").strip().lower()
            if not answer:
                return default
            if answer in yes or answer in no:
                return answer in yes
            self._warn("invalid_choice")

    def _ask_value(self, key, default, is_valid, error_key):
        suffix = f" [{default}]" if default not in (None, "") else ""
        while True:
            answer = self.console.ask(self._wrap(f"{self._t(key)}{suffix}:", "[?]  ") + " ").strip() or (str(default) if default is not None else "")
            if is_valid(answer):
                return answer
            self._warn(error_key)

    def _run_python(self, *args, env=None):
        return self.system.run([self.python, *args], env=env)

    # --- steps ----------------------------------------------------------
    def check_prerequisites(self):
        self._heading(1, "step_prerequisites")
        if self.system_name not in SUPPORTED_PLATFORMS:
            self._error("err_platform")
            return False
        self._ok("ok_platform", system=SUPPORTED_PLATFORMS[self.system_name], store=self._platform_text("store"))
        version = ".".join(map(str, self.system.python_version()))
        if self.system.python_version() < MIN_PYTHON:
            self._error("err_python", required=".".join(map(str, MIN_PYTHON)), version=version)
            return False
        if self.system.python_version()[:2] > MAX_PYTHON:
            self._error("err_python_too_new", maximum=".".join(map(str, MAX_PYTHON)), version=version)
            return False
        self._ok("ok_python", version=version)
        if not self.system.exists(self.root / "requirements.lock"):
            self._error("err_requirements")
            return False
        self._ok("ok_requirements")
        return True

    def install(self):
        self._heading(2, "step_install")
        venv_dir = self.root / "venv"
        reinstall = not self.system.exists(self.python) or self._confirm("venv_exists", default=False, path=venv_dir)
        if reinstall:
            if not self.system.exists(self.python):
                self._say("venv_creating", path=venv_dir)
                code = self.system.run([self.system.python_executable(), "-m", "venv", venv_dir])
                if code:
                    self._error("err_install", code=code)
                    return False
            self._say("deps_installing")
            code = self._run_python("-m", "pip", "install", "--disable-pip-version-check", "-r", self.root / "requirements.lock")
            if code:
                self._error("err_install", code=code)
                return False
            self._ok("ok_install")
        self._say("selftest_running")
        code = self._run_python("-m", "pytest", "-q", "-p", "no:cacheprovider", self.root / "tests")
        if code:
            self._warn("warn_selftest", code=code)
        else:
            self._ok("ok_selftest")
        return True

    def ask_app_id(self):
        self._heading(3, "step_app")
        self._paragraph("app_intro")
        self._say("app_hint")
        low, high = APP_ID_LENGTH
        self.app_id = self._ask_value("ask_app_id", None, lambda v: bool(DIGITS.fullmatch(v)) and low <= len(v) <= high, "err_app_id")
        account = self._ask_value("ask_default_account", "", is_valid_account_id, "err_default_account")
        self.default_account = settings.parse_account_id(account)
        self.instagram = self._confirm("ask_enable_instagram", default=False)
        if self.instagram:
            ig_user = self._ask_value("ask_default_ig_user", "", is_valid_ig_user_id, "err_default_ig_user")
            self.default_ig_user = settings.parse_ig_user_id(ig_user)
        return True

    def store_credentials(self):
        self._heading(4, "step_credentials")
        self._say("credentials_intro")
        self._say("credentials_steps")
        script = self.root / "store_secrets.py"
        already_stored = self._run_python(script, "--status", "--lang", self.lang) == 0
        if already_stored:
            if not self._confirm("credentials_present", default=False):
                return True
        elif not self._confirm("ask_store_credentials"):
            self._say("skip_credentials", python=self.python_hint)
            return True
        if self._run_python(script, "--lang", self.lang) == 0:
            self._ok("ok_credentials", store=self._platform_text("store"))
        else:
            self._warn("warn_credentials", python=self.python_hint)
        return True

    def check_token(self):
        self._heading(5, "step_token")
        self._say("token_checking")
        env = {**os.environ, settings.ENV_ENABLE_INSTAGRAM: "1"} if self.instagram else None
        code = self._run_python(self.root / "check_token.py", "--app-id", self.app_id, "--lang", self.lang, env=env)
        if code == 0:
            self._ok("ok_token")
        elif code == 1:
            self._warn("warn_token")
        else:
            self._error("err_token", python=self.python_hint)
        return True

    def ask_limits(self):
        self._heading(6, "step_limits")
        self._paragraph("limits_intro")
        daily = self._ask_value("ask_daily", DEFAULT_DAILY_LIMIT, is_positive_number, "err_amount")
        lifetime = self._ask_value("ask_lifetime", DEFAULT_LIFETIME_LIMIT, is_positive_number, "err_amount")
        self.limits = (int(daily), int(lifetime))
        domains = self._ask_value("ask_domains", "", is_valid_domain_list, "err_domains")
        self.allowed_domains = ",".join(sorted(url_policy.parse_domains(domains)))
        return True

    def configure_claude(self):
        self._heading(7, "step_claude")
        entry = build_server_entry(self.python, self.root / "launch_secure.py", self.app_id, *self.limits, self.allowed_domains, self.default_account,
                                   self.instagram, self.default_ig_user)
        if self.config_path is None:
            self._warn("claude_config_missing")
            self._json({"mcpServers": {SERVER_NAME: entry}})
            return True
        self._say("claude_config_path", path=self.config_path)
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}
            if not is_valid_config(config):
                raise ValueError("unexpected structure")
        except ValueError:  # includes json.JSONDecodeError
            self._error("err_config_json")
            self._json({"mcpServers": {SERVER_NAME: entry}})
            return True

        self._say("claude_entry_preview", name=SERVER_NAME)
        self._json(entry)
        legacy = legacy_entries(config)
        remove = legacy if legacy and self._confirm("claude_legacy_found", names=", ".join(legacy)) else []
        if not self._confirm("ask_write_config"):
            self._say("skip_config")
            return True
        backup = save_config(self.config_path, config, updated_config(config, entry, remove), self.now())
        self._ok("ok_config")
        if backup:
            self._say("config_backup", path=backup)
            if contains_plaintext_secrets(config):
                self._warn("warn_backup_secrets")
        return True

    def finish(self):
        self.console.say("")
        self._say("finish_title")
        self._say("finish_steps", quit=self._platform_text("quit"), python=self.python_hint)
        return True

    def run(self):
        self.console.say(self._t("wizard_title"))
        self.console.say("=" * WRAP_WIDTH)
        self._paragraph("wizard_intro")
        steps = (self.check_prerequisites, self.install, self.ask_app_id, self.store_credentials,
                 self.check_token, self.ask_limits, self.configure_claude, self.finish)
        try:
            for step in steps:
                if not step():
                    return EXIT_FAILED
        except (EOFError, KeyboardInterrupt):
            self.console.say("")
            self._warn("aborted")
            return EXIT_ABORTED
        return EXIT_OK


def choose_language(console, detected):
    answer = console.ask(f"Language / Sprache [{'/'.join(LANGUAGES)}] ({detected}): ").strip().lower()
    return answer if answer in LANGUAGES else detected


def main(argv=None):
    parser = argparse.ArgumentParser(description="Setup wizard for the meta-ads-mcp secure launcher.")
    add_language_argument(parser)
    parser.add_argument("--claude-config", type=Path, help="path to claude_desktop_config.json (default: auto-detect)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    console = TerminalConsole()
    try:
        lang = args.lang or choose_language(console, detect_language())
    except (EOFError, KeyboardInterrupt):
        return EXIT_ABORTED
    config_path = args.claude_config or find_claude_config(os.environ, platform.system())
    return Wizard(console, LocalSystem(ROOT), lang, config_path).run()


if __name__ == "__main__":
    sys.exit(main())
