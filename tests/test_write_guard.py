import asyncio
import json

import httpx
import pytest

import write_guard

GRAPH = "https://graph.facebook.com/v24.0"
LIMITS = write_guard.DEFAULT_POLICY


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
    limits = write_guard.Policy(max_daily_cents=50_00, max_lifetime_cents=500_00)

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
    assert write_guard.policy_from_env({}) == LIMITS


def test_limits_from_env_converts_major_units_to_cents():
    limits = write_guard.policy_from_env({"META_GUARD_MAX_DAILY_BUDGET": "100", "META_GUARD_MAX_LIFETIME_BUDGET": "2500.50"})

    assert limits.max_daily_cents == 100_00
    assert limits.max_lifetime_cents == 2500_50


@pytest.mark.parametrize("value", ["abc", "-5", "0", ""])
def test_limits_from_env_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="META_GUARD_MAX_DAILY_BUDGET"):
        write_guard.policy_from_env({"META_GUARD_MAX_DAILY_BUDGET": value})


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


# --- Pipeboard egress (roadmap item 2) -------------------------------------

@pytest.mark.parametrize("url", ["https://mcp.pipeboard.co/api/duplicate", "https://meta-ads.mcp.pipeboard.co/x", "https://MCP.PIPEBOARD.CO/x"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_blocks_pipeboard_hosts_for_all_methods(url, method):
    assert "Pipeboard" in check(httpx.Request(method, url))


def test_pipeboard_can_be_allowed_explicitly():
    policy = write_guard.policy_from_env({"META_GUARD_ALLOW_PIPEBOARD": "1"})

    assert check(httpx.Request("POST", "https://mcp.pipeboard.co/x"), policy) is None


def test_custom_pipeboard_base_url_is_blocked_too():
    policy = write_guard.policy_from_env({"PIPEBOARD_API_BASE_URL": "https://pb.internal.example:8443"})

    assert check(httpx.Request("POST", "https://pb.internal.example:8443/x"), policy) is not None


# --- allowed destination domains (roadmap item 6) --------------------------

DOMAINS = write_guard.policy_from_env({"META_GUARD_ALLOWED_URL_DOMAINS": "example.com, Shop.Example.org"})


def creative(**fields):
    return post("act_1/adcreatives", **{key: json.dumps(value) for key, value in fields.items()})


def link_creative(link):
    return creative(object_story_spec={"page_id": "1", "link_data": {"link": link, "message": "Hi",
                                                                      "call_to_action": {"type": "LEARN_MORE", "value": {"link": link}}}})


@pytest.mark.parametrize("link", ["https://example.com/landing", "https://www.example.com/a?b=c", "https://shop.example.org/", "https://EXAMPLE.com./x"])
def test_allows_links_to_allowed_domains(link):
    assert check(link_creative(link), DOMAINS) is None


@pytest.mark.parametrize("link", [
    "https://evil.com/",
    "https://example.com.evil.com/",
    "https://notexample.com/",
    "http://example.com/",
    "https://user@evil.com/",
    "https://example.com@evil.com/",
    "https://93.184.216.34/",
    "https://[2001:db8::1]/",
    "javascript:alert(1)",
    "//evil.com/x",
])
def test_blocks_links_outside_allowed_domains(link):
    reason = check(link_creative(link), DOMAINS)

    assert reason is not None
    assert "destination" in reason


def test_checks_nested_asset_feed_and_video_links():
    asset_feed = {"link_urls": [{"website_url": "https://example.com/", "display_url": "anything.test"},
                                {"website_url": "https://evil.com/"}]}
    video = {"page_id": "1", "video_data": {"video_id": "9", "call_to_action": {"type": "SHOP_NOW", "value": {"link": "https://evil.com/"}}}}

    assert check(creative(asset_feed_spec=asset_feed), DOMAINS) is not None
    assert check(creative(object_story_spec=video), DOMAINS) is not None


def test_ignores_display_url_and_url_tags():
    asset_feed = {"link_urls": [{"website_url": "https://example.com/", "display_url": "elsewhere.test"}]}
    request = post("act_1/adcreatives", asset_feed_spec=json.dumps(asset_feed), url_tags="utm_source=evil.com")

    assert check(request, DOMAINS) is None


def test_checks_top_level_link_fields():
    assert check(post("act_1/adcreatives", link="https://evil.com/"), DOMAINS) is not None


def test_blocks_unparseable_json_when_domain_rule_is_active():
    assert check(post("act_1/adcreatives", object_story_spec="{not json"), DOMAINS) is not None


def test_domain_rule_is_off_by_default():
    assert check(link_creative("https://anything.test/"), LIMITS) is None


def test_domain_rule_ignores_reads():
    assert check(httpx.Request("GET", f"{GRAPH}/act_1/adcreatives", params={"link": "https://evil.com/"}), DOMAINS) is None


@pytest.mark.parametrize("value", ["exa mple.com", "https://example.com", "*.example.com", "-bad.com", ","])
def test_rejects_invalid_domain_configuration(value):
    with pytest.raises(ValueError, match="META_GUARD_ALLOWED_URL_DOMAINS"):
        write_guard.policy_from_env({"META_GUARD_ALLOWED_URL_DOMAINS": value})
