"""Hardened launcher for meta-ads-mcp.

Reads Meta credentials from Windows Credential Manager instead of plain-text
config, disables the local OAuth callback server, hardens the debug log
(see secure_logging) and blocks risky write calls (see write_guard).
"""
import os
import sys

CRED_APP_SECRET = "meta-ads-mcp/app_secret"
CRED_ACCESS_TOKEN = "meta-ads-mcp/access_token"
CRED_TO_ENV = {
    CRED_APP_SECRET: "META_APP_SECRET",
    CRED_ACCESS_TOKEN: "META_ACCESS_TOKEN",
}
HARDENING_ENV = {
    "META_ADS_DISABLE_CALLBACK_SERVER": "1",
    "META_ADS_DISABLE_LOGIN_LINK": "1",
}
ERROR_NOT_FOUND = 1168


def read_windows_credential(target):
    import pywintypes
    import win32cred

    try:
        cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as err:
        if err.winerror == ERROR_NOT_FOUND:
            return None
        raise
    return cred["CredentialBlob"].decode("utf-16-le")


def load_secrets(cred_read=read_windows_credential):
    secrets = {env: cred_read(target) for target, env in CRED_TO_ENV.items()}
    missing = [env for env, value in secrets.items() if not value]
    if missing:
        print(
            f"meta-ads-mcp: credentials missing in Windows Credential Manager: {', '.join(missing)}. "
            "Run 'venv\\Scripts\\python.exe store_secrets.py' (or setup_wizard.py).",
            file=sys.stderr,
        )
        sys.exit(1)
    return secrets


def _config_or_exit(parse, environ):
    try:
        return parse(environ)
    except ValueError as err:
        print(f"meta-ads-mcp: invalid configuration: {err}", file=sys.stderr)
        sys.exit(1)


def load_policy(environ):
    import write_guard

    return _config_or_exit(write_guard.policy_from_env, environ)


def check_extension_config(environ):
    import settings

    def parse(env):
        settings.parse_ig_user_id(env.get(settings.ENV_DEFAULT_IG_USER))
        return settings.parse_account_id(env.get(settings.ENV_DEFAULT_ACCOUNT))

    return _config_or_exit(parse, environ)


def main():
    import secure_logging
    import write_guard

    log_handler = secure_logging.harden_logging()  # before the package import, see secure_logging docstring
    write_guard.install(load_policy(os.environ))
    check_extension_config(os.environ)
    os.environ.update(load_secrets())
    os.environ.update(HARDENING_ENV)

    import meta_ads_mcp  # runs the package's setup_logging()
    from meta_ads_mcp.core.server import mcp_server

    import extensions
    import tool_policy

    secure_logging.adopt_package_logger(log_handler)
    extensions.register(mcp_server)
    tool_policy.apply(mcp_server)
    return meta_ads_mcp.entrypoint()


if __name__ == "__main__":
    sys.exit(main())
