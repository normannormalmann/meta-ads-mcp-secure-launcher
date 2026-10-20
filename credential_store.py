"""Reads and writes the Meta credentials in the operating system's credential store.

Windows: generic credentials in the Windows Credential Manager (via pywin32, imported lazily).
macOS:   generic passwords in the login keychain, via /usr/bin/security.
         Secrets are passed on stdin (`security -i`), never as command-line
         arguments, so they do not show up in the process list.
Apart from pywin32 only the standard library is used.
"""
import re
import subprocess
import sys

CRED_APP_SECRET = "meta-ads-mcp/app_secret"
CRED_ACCESS_TOKEN = "meta-ads-mcp/access_token"
ACCOUNT_NAME = "meta-ads-mcp"
WINDOWS, MACOS = "win32", "darwin"
STORE_NAMES = {WINDOWS: "Windows Credential Manager", MACOS: "macOS Keychain"}

ERROR_NOT_FOUND = 1168  # Windows ERROR_NOT_FOUND
SECURITY_TOOL = "/usr/bin/security"
SECURITY_NOT_FOUND = 44  # exit code of `security find-generic-password` for errSecItemNotFound
SECURITY_TIMEOUT = 60  # seconds; macOS may show an unlock or access dialog first
SAFE_SECRET = re.compile(r"[A-Za-z0-9]+", re.ASCII)  # nothing the `security -i` parser could interpret
# `security -i` reads stdin with its own getchar() loop (SecurityTool/sharedTool/readline.c: no GNU readline,
# no history file) into a fixed buffer; longer lines would be split into two commands (security.c: MAX_LINE_LEN).
SECURITY_MAX_LINE = 4096 - 1


class CredentialReadError(RuntimeError):
    pass


class CredentialWriteError(RuntimeError):
    pass


def store_name(platform=None):
    return STORE_NAMES.get(platform or sys.platform, "credential store")


def _unsupported(platform):
    return f"unsupported platform '{platform}' - only Windows and macOS are supported"


# --- Windows ------------------------------------------------------------
def _read_windows(target):
    try:
        import pywintypes
        import win32cred
    except ImportError:
        raise CredentialReadError("pywin32 is not installed - run the setup wizard") from None
    try:
        cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as err:
        if err.winerror == ERROR_NOT_FOUND:
            return None
        raise CredentialReadError(f"reading '{target}' failed (Windows error {err.winerror})") from None
    try:
        return cred["CredentialBlob"].decode("utf-16-le")
    except UnicodeDecodeError:
        raise CredentialReadError(f"'{target}' is not valid text - store it again") from None


def _write_windows(target, secret):
    try:
        import pywintypes
        import win32cred
    except ImportError:
        raise CredentialWriteError("pywin32 is not installed - run the setup wizard") from None
    try:
        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": target,
                "UserName": ACCOUNT_NAME,
                "CredentialBlob": secret,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
    except pywintypes.error as err:
        raise CredentialWriteError(f"storing '{target}' failed (Windows error {err.winerror})") from None


# --- macOS --------------------------------------------------------------
def _security(args, run, stdin=None):
    # never inherit stdin: in the launcher it is the MCP protocol channel
    stdin_option = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        return run([SECURITY_TOOL, *args], **stdin_option, capture_output=True, encoding="utf-8", errors="replace",
                   timeout=SECURITY_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CredentialReadError("keychain access timed out - unlock the login keychain or answer the "
                                  "macOS access dialog, then try again") from None
    except OSError as err:
        raise CredentialReadError(f"keychain access failed ({type(err).__name__})") from None


def _read_macos(target, run=subprocess.run):
    result = _security(["find-generic-password", "-s", target, "-a", ACCOUNT_NAME, "-w"], run)
    if result.returncode == SECURITY_NOT_FOUND:
        return None
    if result.returncode:
        raise CredentialReadError(f"reading '{target}' from the keychain failed (security exit code {result.returncode})")
    return result.stdout.rstrip("\n")


def _write_macos(target, secret, run=subprocess.run):
    try:
        previous = _read_macos(target, run) if SAFE_SECRET.fullmatch(secret) else None
        stored = _add_macos(target, secret, run)
        if stored == secret:
            return
        restored = previous is not None and _restore_macos(target, previous, run)
    except CredentialReadError as err:
        raise CredentialWriteError(str(err)) from None
    hint = "the previous value is unchanged" if restored else "run store_secrets.py again"
    raise CredentialWriteError(f"storing '{target}' in the keychain failed ({hint})")


def _restore_macos(target, previous, run):
    """-U already replaced the old value; put it back. Returns True if that worked."""
    try:
        return _add_macos(target, previous, run) == previous
    except CredentialWriteError:  # old value not storable through `security -i`
        return False


def _add_macos(target, secret, run):
    """Creates or replaces (-U) the item and returns what the keychain holds afterwards.

    `security -i` does not report failures reliably, hence the read-back.
    """
    if not SAFE_SECRET.fullmatch(secret):
        raise CredentialWriteError(f"'{target}' contains characters that cannot be stored safely")
    command = f"add-generic-password -U -s {target} -a {ACCOUNT_NAME} -l {target} -w {secret}"
    if len(command) >= SECURITY_MAX_LINE:
        raise CredentialWriteError(f"'{target}' is too long for the keychain tool")
    _security(["-i"], run, stdin=command + "\n")
    return _read_macos(target, run)


# --- public API ---------------------------------------------------------
def read_credential(target, platform=None):
    """Returns the stored secret, or None if it does not exist; raises CredentialReadError."""
    platform = platform or sys.platform
    if platform == WINDOWS:
        return _read_windows(target)
    if platform == MACOS:
        return _read_macos(target)
    raise CredentialReadError(_unsupported(platform))


def write_credential(target, secret, platform=None):
    """Creates or replaces the secret; raises CredentialWriteError."""
    platform = platform or sys.platform
    if platform == WINDOWS:
        return _write_windows(target, secret)
    if platform == MACOS:
        return _write_macos(target, secret)
    raise CredentialWriteError(_unsupported(platform))
