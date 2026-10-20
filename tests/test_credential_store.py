import subprocess

import pytest

import credential_store
from conftest import FAKE_TOKEN
from credential_store import CRED_ACCESS_TOKEN, CredentialReadError, CredentialWriteError


class FakeSecurity:
    """Stands in for /usr/bin/security: a keychain dict plus a log of every call."""

    def __init__(self, stored=None, read_code=0, store_on_add=True):
        self.items = dict(stored or {})
        self.read_code = read_code
        self.store_on_add = store_on_add
        self.calls = []

    def __call__(self, args, input=None, **kwargs):
        self.calls.append((args, input))
        if args[1] == "find-generic-password":
            service = args[args.index("-s") + 1]
            if self.read_code:
                return subprocess.CompletedProcess(args, self.read_code, "", "error")
            if service not in self.items:
                return subprocess.CompletedProcess(args, credential_store.SECURITY_NOT_FOUND, "", "not found")
            return subprocess.CompletedProcess(args, 0, self.items[service] + "\n", "")
        if args[1] == "-i":
            words = input.split()
            if self.store_on_add:
                self.items[words[words.index("-s") + 1]] = words[words.index("-w") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected call {args}")


def test_macos_read_returns_value_without_trailing_newline():
    security = FakeSecurity({CRED_ACCESS_TOKEN: FAKE_TOKEN})

    assert credential_store._read_macos(CRED_ACCESS_TOKEN, security) == FAKE_TOKEN
    [(args, _)] = security.calls
    assert args[0] == "/usr/bin/security"
    assert args[args.index("-a") + 1] == "meta-ads-mcp"


def test_macos_read_returns_none_when_missing():
    assert credential_store._read_macos(CRED_ACCESS_TOKEN, FakeSecurity()) is None


def test_macos_read_error_is_reported_without_details():
    with pytest.raises(CredentialReadError, match="exit code 51"):
        credential_store._read_macos(CRED_ACCESS_TOKEN, FakeSecurity(read_code=51))


@pytest.mark.parametrize("error", [FileNotFoundError("security"), subprocess.TimeoutExpired("security", 60)])
def test_macos_tool_failures_become_read_errors(error):
    def broken(*args, **kwargs):
        raise error

    with pytest.raises(CredentialReadError):
        credential_store._read_macos(CRED_ACCESS_TOKEN, broken)


def test_macos_write_passes_secret_on_stdin_only_and_verifies():
    security = FakeSecurity()

    credential_store._write_macos(CRED_ACCESS_TOKEN, FAKE_TOKEN, security)

    assert security.items == {CRED_ACCESS_TOKEN: FAKE_TOKEN}
    assert all(FAKE_TOKEN not in " ".join(args) for args, _ in security.calls)
    [(_, add_input)] = [(args, stdin) for args, stdin in security.calls if args[1:] == ["-i"]]
    assert "add-generic-password -U" in add_input and FAKE_TOKEN in add_input


def test_macos_write_fails_when_value_was_not_stored():
    with pytest.raises(CredentialWriteError):
        credential_store._write_macos(CRED_ACCESS_TOKEN, FAKE_TOKEN, FakeSecurity(store_on_add=False))


@pytest.mark.parametrize("secret", ["abc def", 'abc"; delete-keychain', "abc\nadd-generic-password", ""])
def test_macos_write_rejects_unsafe_characters_before_calling_security(secret):
    security = FakeSecurity()

    with pytest.raises(CredentialWriteError):
        credential_store._write_macos(CRED_ACCESS_TOKEN, secret, security)
    assert security.calls == []


def test_unsupported_platform_is_rejected():
    with pytest.raises(CredentialReadError, match="Windows and macOS"):
        credential_store.read_credential(CRED_ACCESS_TOKEN, platform="linux")
    with pytest.raises(CredentialWriteError, match="Windows and macOS"):
        credential_store.write_credential(CRED_ACCESS_TOKEN, FAKE_TOKEN, platform="linux")


@pytest.mark.parametrize("platform, expected", [("win32", "Windows Credential Manager"), ("darwin", "macOS Keychain")])
def test_store_name(platform, expected):
    assert credential_store.store_name(platform) == expected


def test_macos_reads_never_inherit_the_mcp_stdin():
    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, credential_store.SECURITY_NOT_FOUND, "", "")

    credential_store._read_macos(CRED_ACCESS_TOKEN, run)

    assert seen["stdin"] is subprocess.DEVNULL
    assert "input" not in seen


def test_macos_failed_write_restores_previous_value():
    class TruncatingSecurity(FakeSecurity):
        def __call__(self, args, input=None, **kwargs):
            if args[1] == "-i" and FAKE_TOKEN in input:
                input = input.replace(FAKE_TOKEN, FAKE_TOKEN[:20])  # simulate a cut-off line
            return super().__call__(args, input, **kwargs)

    security = TruncatingSecurity({CRED_ACCESS_TOKEN: "EAAoldtoken"})

    with pytest.raises(CredentialWriteError):
        credential_store._write_macos(CRED_ACCESS_TOKEN, FAKE_TOKEN, security)
    assert security.items == {CRED_ACCESS_TOKEN: "EAAoldtoken"}


def test_macos_timeout_explains_what_to_do():
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("security", 60)

    with pytest.raises(CredentialReadError, match="unlock the login keychain"):
        credential_store._read_macos(CRED_ACCESS_TOKEN, slow)


def test_macos_write_rejects_lines_security_would_split():
    security = FakeSecurity({CRED_ACCESS_TOKEN: "EAAoldtoken"})

    with pytest.raises(CredentialWriteError, match="too long"):
        credential_store._write_macos(CRED_ACCESS_TOKEN, "EAA" + "x" * credential_store.SECURITY_MAX_LINE, security)
    assert all(args[1:] != ["-i"] for args, _ in security.calls)
    assert security.items == {CRED_ACCESS_TOKEN: "EAAoldtoken"}


class IgnoringSecurity(FakeSecurity):
    """`security -i` that silently stores nothing."""

    def __init__(self, stored):
        super().__init__(stored, store_on_add=False)


def test_macos_restore_never_passes_unsafe_old_values_to_security():
    security = IgnoringSecurity({CRED_ACCESS_TOKEN: "old value\nwith-newline"})

    with pytest.raises(CredentialWriteError, match="run store_secrets.py again"):
        credential_store._write_macos(CRED_ACCESS_TOKEN, FAKE_TOKEN, security)
    assert all("with-newline" not in (stdin or "") for _, stdin in security.calls)


def test_macos_failed_write_reports_unchanged_previous_value():
    security = IgnoringSecurity({CRED_ACCESS_TOKEN: "EAAoldtoken"})

    with pytest.raises(CredentialWriteError, match="previous value is unchanged") as exc:
        credential_store._write_macos(CRED_ACCESS_TOKEN, FAKE_TOKEN, security)
    assert "EAAoldtoken" not in str(exc.value) and FAKE_TOKEN not in str(exc.value)
