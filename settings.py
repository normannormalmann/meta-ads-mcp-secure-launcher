"""Configuration values shared by the launcher and the setup wizard (stdlib only)."""
import re

ENV_DEFAULT_ACCOUNT = "META_DEFAULT_ACCOUNT_ID"
ENV_ENABLE_INSTAGRAM = "META_ENABLE_INSTAGRAM_INSIGHTS"
ENV_DEFAULT_IG_USER = "META_DEFAULT_IG_USER_ID"
ACCOUNT_PATTERN = re.compile(r"(?:act_)?([0-9]+)", re.ASCII)
IG_USER_PATTERN = re.compile(r"[0-9]{5,25}", re.ASCII)
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def parse_account_id(raw):
    """Normalizes '123' / 'act_123' to 'act_123'; empty -> None; invalid -> ValueError."""
    if raw is None or not raw.strip():
        return None
    match = ACCOUNT_PATTERN.fullmatch(raw.strip())
    if not match:
        raise ValueError(f"{ENV_DEFAULT_ACCOUNT} must look like 'act_1234567890', got {raw!r}.")
    return f"act_{match.group(1)}"


def parse_ig_user_id(raw):
    """Instagram user IDs are numeric (e.g. 17841400000000000); empty -> None."""
    if raw is None or not raw.strip():
        return None
    if not IG_USER_PATTERN.fullmatch(raw.strip()):
        raise ValueError(f"{ENV_DEFAULT_IG_USER} must be the numeric Instagram user ID, got {raw!r}.")
    return raw.strip()


def is_enabled(raw):
    return bool(raw) and raw.strip().lower() in TRUE_VALUES
