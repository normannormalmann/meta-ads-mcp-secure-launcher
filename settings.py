"""Configuration values shared by the launcher and the setup wizard (stdlib only)."""
import re

ENV_DEFAULT_ACCOUNT = "META_DEFAULT_ACCOUNT_ID"
ACCOUNT_PATTERN = re.compile(r"(?:act_)?(\d+)")


def parse_account_id(raw):
    """Normalizes '123' / 'act_123' to 'act_123'; empty -> None; invalid -> ValueError."""
    if raw is None or not raw.strip():
        return None
    match = ACCOUNT_PATTERN.fullmatch(raw.strip())
    if not match:
        raise ValueError(f"{ENV_DEFAULT_ACCOUNT} must look like 'act_1234567890', got {raw!r}.")
    return f"act_{match.group(1)}"
