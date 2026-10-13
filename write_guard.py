"""Local safety net for requests of meta-ads-mcp.

Hooks httpx.AsyncClient.send, which every Graph API and Pipeboard request of
meta-ads-mcp passes through (asserted by tests/test_upstream_invariants.py), and
rejects:
- Graph requests with unexpected paths or request-override parameters
  (method, batch, ...), which could smuggle a different operation past the rules,
- writes that exceed budget limits or would delete or archive objects, checked
  for every value of a key and inside JSON-encoded fields,
- ad destinations outside the allowed domains (if configured, see url_policy),
- any request to Pipeboard servers unless explicitly allowed.
Blocked calls never leave the machine; Claude receives a Graph-style error
explaining the rule. Budget limits apply per object, in the ad account currency.
"""
import json
import logging
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

import form_fields
import settings
import url_policy

ENV_MAX_DAILY = "META_GUARD_MAX_DAILY_BUDGET"
ENV_MAX_LIFETIME = "META_GUARD_MAX_LIFETIME_BUDGET"
ENV_ALLOW_PIPEBOARD = "META_GUARD_ALLOW_PIPEBOARD"
ENV_PIPEBOARD_BASE_URL = "PIPEBOARD_API_BASE_URL"
CENTS_PER_UNIT = 100
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

GRAPH_HOSTS = frozenset({"graph.facebook.com", "graph-video.facebook.com"})
GRAPH_PATH = re.compile(r"/v\d+\.\d+(/[A-Za-z0-9_]+)*/?", re.ASCII)
OVERRIDE_KEYS = frozenset({"method", "_method", "batch", "include_headers", "suppress_http_code"})
AUTH_QUERY_KEYS = frozenset({"access_token", "appsecret_proof"})
WRITE_METHODS = frozenset({"POST", "PUT"})
DAILY_FIELDS = frozenset({"daily_budget", "daily_spend_cap", "daily_min_spend_target"})
LIFETIME_FIELDS = frozenset({"lifetime_budget", "spend_cap", "lifetime_spend_cap", "lifetime_min_spend_target"})
STATUS_FIELDS = frozenset({"status", "configured_status"})
CENTS = re.compile(r"-?[0-9]+", re.ASCII)

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
        amount = math.nan
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError(f"{name} must be a positive amount in account currency, got '{raw}'.")
    return round(amount * CENTS_PER_UNIT)


def _blocked_hosts(environ):
    if settings.is_enabled(environ.get(ENV_ALLOW_PIPEBOARD)):
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


def _cents(value):
    """Amount in cents from a form string or JSON number; None if not a plain integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    text = str(value).strip()
    return int(text) if CENTS.fullmatch(text) else None


def _check_amounts(pairs, policy):
    for key, value in pairs:
        limit = policy.max_daily_cents if key in DAILY_FIELDS else policy.max_lifetime_cents if key in LIFETIME_FIELDS else None
        if limit is None:
            continue
        amount = _cents(value)
        if amount is None:
            return f"{key}='{value}' is not a valid amount in cents."
        if amount > limit:
            return f"{key} {_units(amount)} exceeds the limit of {_units(limit)} (account currency)."
    return None


def _check_statuses(pairs):
    for key, value in pairs:
        status = str(value).strip().upper()
        if key in STATUS_FIELDS and status in BLOCKED_STATUSES:
            return f"Setting status {status} (delete/archive) is blocked. Please do this in Meta Ads Manager."
    return None


def _check_budget_schedules(pairs, policy):
    value_types = {str(value).strip().upper() for key, value in pairs if key == "budget_value_type"}
    for key, value in pairs:
        if key != "budget_value":
            continue
        if "ABSOLUTE" in value_types or not value_types:
            reason = _check_amounts([("daily_budget", value)], policy)
            if reason:
                return reason.replace("daily_budget", "budget_value", 1)
        if "MULTIPLIER" in value_types:
            try:
                multiplier = float(value)
            except (TypeError, ValueError):
                multiplier = math.nan
            if not math.isfinite(multiplier) or multiplier > policy.max_budget_multiplier:
                return f"Budget multiplier '{value}' exceeds the limit of {policy.max_budget_multiplier}."
    return None


def _check_request_shape(request, pairs):
    if not GRAPH_PATH.fullmatch(request.url.path):
        return f"Unexpected Graph API path '{request.url.path[:80]}' - blocked for safety."
    overrides = sorted({key for key, _ in pairs if key.lower() in OVERRIDE_KEYS})
    if overrides:
        return f"Request parameter(s) {', '.join(overrides)} not allowed: they could change the operation."
    if request.method == "POST":
        extra = sorted({key for key, _ in request.url.params.multi_items()} - AUTH_QUERY_KEYS)
        if extra:
            return f"Query parameter(s) {', '.join(extra)} not allowed on POST requests."
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
    pairs = form_fields.request_pairs(request)
    if pairs is None:
        return "Unknown or undecodable request format - blocked for safety."
    reason = _check_request_shape(request, pairs)
    if reason or request.method not in WRITE_METHODS:
        return reason
    expanded = form_fields.all_pairs(pairs)
    return (
        _check_statuses(expanded)
        or _check_amounts(expanded, policy)
        or _check_budget_schedules(pairs, policy)
        or url_policy.check_destinations(pairs, policy.allowed_url_domains)
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
