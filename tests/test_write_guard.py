import asyncio
import json

import httpx
import pytest

import write_guard

GRAPH = "https://graph.facebook.com/v24.0"
LIMITS = write_guard.DEFAULT_LIMITS


def post(path, **data):
    return httpx.Request("POST", f"{GRAPH}/{path}", data=data)


def check(request, limits=LIMITS):
    return write_guard.check_request(request, limits)


@pytest.mark.parametrize("field", ["daily_budget", "daily_spend_cap", "daily_min_spend_target"])
def test_blocks_daily_budget_above_limit(field):
    request = post("act_1/campaigns", **{field: str(LIMITS.max_daily_cents + 1)})

    assert "250" in check(request)


def test_allows_daily_budget_at_limit():
    assert check(post("123", daily_budget=str(LIMITS.max_daily_cents))) is None


@pytest.mark.parametrize("field", ["lifetime_budget", "spend_cap", "lifetime_spend_cap", "lifetime_min_spend_target"])
def test_blocks_lifetime_budget_above_limit(field):
    request = post("123", **{field: str(LIMITS.max_lifetime_cents + 1)})

    assert "5000" in check(request)


def test_allows_lifetime_budget_at_limit():
    assert check(post("123", lifetime_budget=str(LIMITS.max_lifetime_cents))) is None


def test_custom_limits_are_applied():
    limits = write_guard.Limits(max_daily_cents=50_00, max_lifetime_cents=500_00)

    assert check(post("123", daily_budget="5001"), limits) is not None
    assert check(post("123", daily_budget="5000"), limits) is None


def test_blocks_non_numeric_budget():
    assert check(post("123", daily_budget="a lot")) is not None


@pytest.mark.parametrize("status", ["DELETED", "ARCHIVED", "deleted"])
def test_blocks_irreversible_status(status):
    assert status.upper() in check(post("123", status=status))


@pytest.mark.parametrize("status", ["PAUSED", "ACTIVE"])
def test_allows_pause_and_activate(status):
    assert check(post("123", status=status)) is None


def test_blocks_http_delete():
    assert check(httpx.Request("DELETE", f"{GRAPH}/123")) is not None


def test_blocks_absolute_budget_schedule_above_daily_limit():
    request = post("123/budget_schedules", budget_value=str(LIMITS.max_daily_cents + 1), budget_value_type="ABSOLUTE")

    assert check(request) is not None


def test_blocks_multiplier_budget_schedule_above_limit():
    assert check(post("123/budget_schedules", budget_value="5", budget_value_type="MULTIPLIER")) is not None


def test_checks_put_query_params_too():
    assert check(httpx.Request("PUT", f"{GRAPH}/123", params={"daily_budget": "999999"})) is not None


def test_blocks_unknown_body_format_on_write():
    request = httpx.Request("POST", f"{GRAPH}/123", content=b'{"daily_budget": 999999}', headers={"content-type": "application/json"})

    assert check(request) is not None


def test_ignores_reads_and_other_hosts():
    assert check(httpx.Request("GET", f"{GRAPH}/123", params={"status": "DELETED"})) is None
    assert check(httpx.Request("POST", "https://example.com/x", data={"status": "DELETED"})) is None


def test_allows_harmless_write():
    assert check(post("act_1/campaigns", name="Test", objective="OUTCOME_TRAFFIC")) is None


def test_limits_from_env_uses_defaults_when_unset():
    assert write_guard.limits_from_env({}) == LIMITS


def test_limits_from_env_converts_major_units_to_cents():
    limits = write_guard.limits_from_env({"META_GUARD_MAX_DAILY_BUDGET": "100", "META_GUARD_MAX_LIFETIME_BUDGET": "2500.50"})

    assert limits.max_daily_cents == 100_00
    assert limits.max_lifetime_cents == 2500_50


@pytest.mark.parametrize("value", ["abc", "-5", "0", ""])
def test_limits_from_env_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="META_GUARD_MAX_DAILY_BUDGET"):
        write_guard.limits_from_env({"META_GUARD_MAX_DAILY_BUDGET": value})


def test_installed_guard_stops_request_before_network():
    sent = []
    transport = httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json={"id": "1"}))

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            blocked = await client.post(f"{GRAPH}/123", data={"status": "DELETED"})
            allowed = await client.post(f"{GRAPH}/123", data={"status": "PAUSED"})
        return blocked, allowed

    write_guard.install(LIMITS)
    try:
        blocked, allowed = asyncio.run(run())
    finally:
        write_guard.uninstall()

    assert blocked.status_code == 403
    assert "local write guard" in json.loads(blocked.text)["error"]["message"]
    assert allowed.status_code == 200
    assert len(sent) == 1
