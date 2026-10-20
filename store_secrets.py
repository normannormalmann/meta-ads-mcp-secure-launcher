"""Stores the Meta app secret and access token in the Windows Credential Manager or the macOS Keychain.

Run interactively: <venv python> store_secrets.py [--lang de|en]
       Status only: <venv python> store_secrets.py --status
<venv python> is venv\\Scripts\\python.exe on Windows and venv/bin/python on macOS.
Input is hidden and never written to disk, shell history or command lines.
"""
import argparse
import getpass
import re
import sys

from credential_store import (CRED_ACCESS_TOKEN, CRED_APP_SECRET, CredentialReadError, CredentialWriteError,
                              read_credential, store_name, write_credential)
from i18n import add_language_argument, detect_language, platform_text, t

PROMPT_KEYS = {
    CRED_APP_SECRET: "prompt_app_secret",
    CRED_ACCESS_TOKEN: "prompt_access_token",
}
FORMAT_RULES = {
    CRED_APP_SECRET: (re.compile(r"[0-9a-f]{32}"), "error_app_secret_format"),
    CRED_ACCESS_TOKEN: (re.compile(r"EAA[A-Za-z0-9]{50,}"), "error_token_format"),
}
MAX_ATTEMPTS = 3
VISIBLE_TAIL = 4


def validate(target, secret, lang):
    """Returns an error message, or None if the secret has the expected format."""
    pattern, message_key = FORMAT_RULES[target]
    return None if pattern.fullmatch(secret) else t(lang, message_key)


def describe(secret, lang):
    return t(lang, "secret_received", length=len(secret), tail=secret[-VISIBLE_TAIL:])


def prompt_secret(target, lang):
    for _ in range(MAX_ATTEMPTS):
        secret = getpass.getpass(t(lang, PROMPT_KEYS[target], paste=platform_text(lang, "paste"))).strip()
        if not secret:
            print(t(lang, "secret_empty"))
            continue
        error = validate(target, secret, lang)
        if error:
            print(t(lang, "secret_invalid", description=describe(secret, lang), error=error))
            continue
        print(t(lang, "secret_ok", description=describe(secret, lang)))
        return secret
    return None


def print_status(cred_read, lang):
    """Prints which credentials exist (never their values). Exit code 0 if all exist."""
    all_present = True
    for target in PROMPT_KEYS:
        present = bool(cred_read(target))
        all_present = all_present and present
        print(t(lang, "status_present" if present else "status_missing", target=target))
    return 0 if all_present else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Store Meta credentials in the {store_name()}.")
    add_language_argument(parser)
    parser.add_argument("--status", action="store_true", help="only show whether credentials are stored")
    args = parser.parse_args(argv)
    lang = detect_language(args.lang)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    if args.status:
        try:
            return print_status(read_credential, lang)
        except CredentialReadError as err:
            print(f"meta-ads-mcp: {err}", file=sys.stderr)
            return 2
    for target in PROMPT_KEYS:
        secret = prompt_secret(target, lang)
        if secret is None:
            print(t(lang, "secret_aborted", target=target), file=sys.stderr)
            return 1
        try:
            write_credential(target, secret)
        except CredentialWriteError as err:
            print(f"meta-ads-mcp: {err}", file=sys.stderr)
            return 2
        print(t(lang, "secret_saved", target=target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
