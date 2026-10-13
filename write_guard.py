"""Local safety net for write calls to the Meta Graph API.

Hooks httpx.AsyncClient.send, which every Graph API request of meta-ads-mcp
passes through, and rejects writes that exceed budget limits or would delete
or archive objects. Blocked calls never leave the machine; Claude receives a
Graph-style error explaining the rule. Limits apply per request/object, in the
ad account currency.
"""
import json
import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl

import httpx

ENV_MAX_DAILY = "META_GUARD_MAX_DAILY_BUDGET"
ENV_MAX_LIFETIME = "META_GUARD_MAX_LIFETIME_BUDGET"
CENTS_PER_UNIT = 100


@dataclass(frozen=True)
class Limits:
    max_daily_cents: int
    max_lifetime_cents: int
    max_budget_multiplier: float = 2.0


DEFAULT_LIMITS = Limits(max_daily_cents=250 * CENTS_PER_UNIT, max_lifetime_cents=5_000 * CENTS_PER_UNIT)
BLOCKED_STATUSES = frozenset({"DELETED", "ARCHIVED"})

GRAPH_HOSTS = frozenset({"graph.facebook.com"})
WRITE_METHODS = frozenset({"POST", "PUT"})
DAILY_FIELDS = ("daily_budget", "daily_spend_cap", "daily_min_spend_target")
LIFETIME_FIELDS = ("lifetime_budget", "spend_cap", "lifetime_spend_cap", "lifetime_min_spend_target")
STATUS_FIELDS = ("status", "configured_status")
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

logger = logging.getLogger("meta-ads-mcp.write-guard")
_original_send = None
_active_limits = DEFAULT_LIMITS


def _limit_cents(environ, name, default_cents):
    raw = environ.get(name)
    if raw is None:
        return default_cents
    try:
        amount = float(raw)
    except ValueError:
        amount = 0
    if amount <= 0:
        raise ValueError(f"{name} must be a positive amount in account currency, got '{raw}'.")
    return round(amount * CENTS_PER_UNIT)


def limits_from_env(environ):
    return Limits(
        max_daily_cents=_limit_cents(environ, ENV_MAX_DAILY, DEFAULT_LIMITS.max_daily_cents),
        max_lifetime_cents=_limit_cents(environ, ENV_MAX_LIFETIME, DEFAULT_LIMITS.max_lifetime_cents),
    )


def _units(cents):
    return f"{cents / CENTS_PER_UNIT:.2f}"


def _request_fields(request):
    """Returns query + form fields, or None if the body format is unknown."""
    fields = dict(request.url.params)
    body = request.read()
    if not body:
        return fields
    if not request.headers.get("content-type", "").startswith(FORM_CONTENT_TYPE):
        return None
    return {**fields, **dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))}


def _check_amount(fields, names, limit_cents):
    for name in names:
        if name not in fields:
            continue
        try:
            amount = int(fields[name])
        except ValueError:
            return f"{name}='{fields[name]}' is not a valid amount in cents."
        if amount > limit_cents:
            return f"{name} {_units(amount)} exceeds the limit of {_units(limit_cents)} (account currency)."
    return None


def _check_status(fields):
    for name in STATUS_FIELDS:
        status = str(fields.get(name, "")).upper()
        if status in BLOCKED_STATUSES:
            return f"Setting status {status} (delete/archive) is blocked. Please do this in Meta Ads Manager."
    return None


def _check_budget_schedule(fields, limits):
    value_type = str(fields.get("budget_value_type", "")).upper()
    if "budget_value" not in fields or not value_type:
        return None
    if value_type == "ABSOLUTE":
        return _check_amount(fields, ("budget_value",), limits.max_daily_cents)
    try:
        multiplier = float(fields["budget_value"])
    except ValueError:
        return f"budget_value='{fields['budget_value']}' is not a valid number."
    if multiplier > limits.max_budget_multiplier:
        return f"Budget multiplier {multiplier} exceeds the limit of {limits.max_budget_multiplier}."
    return None


def check_request(request, limits=DEFAULT_LIMITS):
    """Returns a reason string if the request must be blocked, else None."""
    if request.url.host not in GRAPH_HOSTS:
        return None
    if request.method == "DELETE":
        return "Deleting via the API is blocked. Please do this in Meta Ads Manager."
    if request.method not in WRITE_METHODS:
        return None
    fields = _request_fields(request)
    if fields is None:
        return "Unknown request format for a write call - blocked for safety."
    return (
        _check_status(fields)
        or _check_amount(fields, DAILY_FIELDS, limits.max_daily_cents)
        or _check_amount(fields, LIFETIME_FIELDS, limits.max_lifetime_cents)
        or _check_budget_schedule(fields, limits)
    )


def _blocked_response(request, reason):
    error = {"message": f"Blocked by local write guard: {reason}", "type": "LocalWriteGuard", "code": 403}
    return httpx.Response(403, content=json.dumps({"error": error}), headers={"content-type": "application/json"}, request=request)


async def _guarded_send(self, request, **kwargs):
    reason = check_request(request, _active_limits)
    if reason:
        logger.warning("Blocked %s %s: %s", request.method, request.url.path, reason)
        return _blocked_response(request, reason)
    return await _original_send(self, request, **kwargs)


def install(limits=DEFAULT_LIMITS):
    global _original_send, _active_limits
    _active_limits = limits
    if _original_send is None:
        _original_send = httpx.AsyncClient.send
        httpx.AsyncClient.send = _guarded_send


def uninstall():
    global _original_send, _active_limits
    if _original_send is not None:
        httpx.AsyncClient.send = _original_send
        _original_send = None
    _active_limits = DEFAULT_LIMITS
