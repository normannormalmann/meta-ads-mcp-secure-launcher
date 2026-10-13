"""Local safety net for requests of meta-ads-mcp.

Hooks httpx.AsyncClient.send, which every Graph API and Pipeboard request of
meta-ads-mcp passes through (asserted by tests/test_upstream_invariants.py), and
rejects:
- writes that exceed budget limits or would delete or archive objects,
- ad destinations outside the allowed domains (if configured, see url_policy),
- any request to Pipeboard servers unless explicitly allowed.
Blocked calls never leave the machine; Claude receives a Graph-style error
explaining the rule. Budget limits apply per request/object, in the ad account
currency.
"""
import json
import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

import httpx

import url_policy

ENV_MAX_DAILY = "META_GUARD_MAX_DAILY_BUDGET"
ENV_MAX_LIFETIME = "META_GUARD_MAX_LIFETIME_BUDGET"
ENV_ALLOW_PIPEBOARD = "META_GUARD_ALLOW_PIPEBOARD"
ENV_PIPEBOARD_BASE_URL = "PIPEBOARD_API_BASE_URL"
CENTS_PER_UNIT = 100
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
PIPEBOARD_HOSTS = frozenset({"mcp.pipeboard.co", "meta-ads.mcp.pipeboard.co"})


@dataclass(frozen=True)
class Policy:
    max_daily_cents: int
    max_lifetime_cents: int
    max_budget_multiplier: float = 2.0
    blocked_hosts: frozenset = PIPEBOARD_HOSTS
    allowed_url_domains: frozenset = frozenset()


DEFAULT_POLICY = Policy(max_daily_cents=250 * CENTS_PER_UNIT, max_lifetime_cents=5_000 * CENTS_PER_UNIT)
BLOCKED_STATUSES = frozenset({"DELETED", "ARCHIVED"})

GRAPH_HOSTS = frozenset({"graph.facebook.com"})
WRITE_METHODS = frozenset({"POST", "PUT"})
DAILY_FIELDS = ("daily_budget", "daily_spend_cap", "daily_min_spend_target")
LIFETIME_FIELDS = ("lifetime_budget", "spend_cap", "lifetime_spend_cap", "lifetime_min_spend_target")
STATUS_FIELDS = ("status", "configured_status")
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

logger = logging.getLogger("meta-ads-mcp.write-guard")
_original_send = None
_active_policy = DEFAULT_POLICY


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


def _blocked_hosts(environ):
    if environ.get(ENV_ALLOW_PIPEBOARD, "").strip().lower() in TRUE_VALUES:
        return frozenset()
    custom_host = urlsplit(environ.get(ENV_PIPEBOARD_BASE_URL, "")).hostname
    return PIPEBOARD_HOSTS | ({custom_host.lower()} if custom_host else set())


def policy_from_env(environ):
    """Builds the policy from environment variables; raises ValueError on invalid values."""
    return Policy(
        max_daily_cents=_limit_cents(environ, ENV_MAX_DAILY, DEFAULT_POLICY.max_daily_cents),
        max_lifetime_cents=_limit_cents(environ, ENV_MAX_LIFETIME, DEFAULT_POLICY.max_lifetime_cents),
        blocked_hosts=_blocked_hosts(environ),
        allowed_url_domains=url_policy.parse_domains(environ.get(url_policy.ENV_ALLOWED_DOMAINS)),
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


def _check_budget_schedule(fields, policy):
    value_type = str(fields.get("budget_value_type", "")).upper()
    if "budget_value" not in fields or not value_type:
        return None
    if value_type == "ABSOLUTE":
        return _check_amount(fields, ("budget_value",), policy.max_daily_cents)
    try:
        multiplier = float(fields["budget_value"])
    except ValueError:
        return f"budget_value='{fields['budget_value']}' is not a valid number."
    if multiplier > policy.max_budget_multiplier:
        return f"Budget multiplier {multiplier} exceeds the limit of {policy.max_budget_multiplier}."
    return None


def check_request(request, policy=DEFAULT_POLICY):
    """Returns a reason string if the request must be blocked, else None."""
    host = (request.url.host or "").lower()
    if host in policy.blocked_hosts:
        return (f"Requests to Pipeboard servers ({host}) are blocked to keep campaign data local. "
                f"Set {ENV_ALLOW_PIPEBOARD}=1 to allow them.")
    if host not in GRAPH_HOSTS:
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
        or _check_amount(fields, DAILY_FIELDS, policy.max_daily_cents)
        or _check_amount(fields, LIFETIME_FIELDS, policy.max_lifetime_cents)
        or _check_budget_schedule(fields, policy)
        or url_policy.check_destinations(fields, policy.allowed_url_domains)
    )


def _blocked_response(request, reason):
    error = {"message": f"Blocked by local write guard: {reason}", "type": "LocalWriteGuard", "code": 403}
    return httpx.Response(403, content=json.dumps({"error": error}), headers={"content-type": "application/json"}, request=request)


async def _guarded_send(self, request, **kwargs):
    reason = check_request(request, _active_policy)
    if reason:
        logger.warning("Blocked %s %s%s: %s", request.method, request.url.host, request.url.path, reason)
        return _blocked_response(request, reason)
    return await _original_send(self, request, **kwargs)


def install(policy=DEFAULT_POLICY):
    global _original_send, _active_policy
    _active_policy = policy
    if _original_send is None:
        _original_send = httpx.AsyncClient.send
        httpx.AsyncClient.send = _guarded_send


def uninstall():
    global _original_send, _active_policy
    if _original_send is not None:
        httpx.AsyncClient.send = _original_send
        _original_send = None
    _active_policy = DEFAULT_POLICY
