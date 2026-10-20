"""Hardened launcher for meta-ads-mcp.

Reads Meta credentials from the Windows Credential Manager or the macOS
Keychain (see credential_store) instead of plain-text config, disables the
local OAuth callback server, hardens the debug log (see secure_logging) and
blocks risky write calls (see write_guard).
Every startup problem ends with a one-line message on stderr and exit code 1;
stdout stays reserved for the MCP protocol.
"""
import os
import sys

from credential_store import CRED_ACCESS_TOKEN, CRED_APP_SECRET, CredentialReadError, read_credential, store_name

CRED_TO_ENV = {
    CRED_APP_SECRET: "META_APP_SECRET",
    CRED_ACCESS_TOKEN: "META_ACCESS_TOKEN",
}
HARDENING_ENV = {
    "META_ADS_DISABLE_CALLBACK_SERVER": "1",
    "META_ADS_DISABLE_LOGIN_LINK": "1",
}
SETUP_HINT = "Run store_secrets.py with the venv Python (or setup_wizard.py)."
REINSTALL_HINT = ("Reinstall with '<venv python> -m pip install -r requirements.lock' "
                  "and run '<venv python> -m pytest -q'.")


class CredentialsMissing(RuntimeError):
    def __init__(self, missing):
        super().__init__(f"credentials missing in {store_name()}: {', '.join(missing)}")
        self.missing = missing


def _fail(message):
    print(f"meta-ads-mcp: {message}", file=sys.stderr)
    sys.exit(1)


def read_secrets(cred_read=read_credential):
    """Returns {env name: value}; raises CredentialsMissing or CredentialReadError."""
    secrets = {env: cred_read(target) for target, env in CRED_TO_ENV.items()}
    missing = [env for env, value in secrets.items() if not value]
    if missing:
        raise CredentialsMissing(missing)
    return secrets


def load_secrets(cred_read=read_credential):
    try:
        return read_secrets(cred_read)
    except (CredentialsMissing, CredentialReadError) as err:
        _fail(f"{err}. {SETUP_HINT}")


def _config_or_exit(parse, environ):
    try:
        return parse(environ)
    except ValueError as err:
        _fail(f"invalid configuration: {err}")


def load_policy(environ):
    import write_guard

    return _config_or_exit(write_guard.policy_from_env, environ)


def check_extension_config(environ):
    import settings

    def parse(env):
        settings.parse_ig_user_id(env.get(settings.ENV_DEFAULT_IG_USER))
        return settings.parse_account_id(env.get(settings.ENV_DEFAULT_ACCOUNT))

    return _config_or_exit(parse, environ)


def wire_upstream(log_handler, environ):
    """Imports meta-ads-mcp, adds launcher tools and annotations; returns its entrypoint."""
    try:
        import meta_ads_mcp  # runs the package's setup_logging()
        from meta_ads_mcp.core.server import mcp_server

        import extensions
        import secure_logging
        import tool_policy

        if log_handler is not None:
            secure_logging.adopt_package_logger(log_handler)
        extensions.register(mcp_server, environ)
        tool_policy.apply(mcp_server)
        return meta_ads_mcp.entrypoint
    except (ImportError, AttributeError) as err:
        _fail(f"unsupported or broken meta-ads-mcp installation ({type(err).__name__}: {err}). {REINSTALL_HINT}")


def main():
    import secure_logging
    import write_guard

    try:
        log_handler = secure_logging.harden_logging()  # before the package import, see secure_logging docstring
    except OSError as err:
        _fail(f"cannot open the log file ({err}).")
    write_guard.install(load_policy(os.environ))
    check_extension_config(os.environ)
    os.environ.update(load_secrets())
    os.environ.update(HARDENING_ENV)
    entrypoint = wire_upstream(log_handler, os.environ)
    return entrypoint()


if __name__ == "__main__":
    sys.exit(main())
