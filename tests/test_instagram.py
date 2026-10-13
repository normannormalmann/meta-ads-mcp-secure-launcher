import asyncio
import json
from datetime import datetime, timezone

import pytest
from mcp.server.fastmcp import FastMCP

import instagram
import settings
import tool_policy
from graph_client import GraphError

IG = "17841400000000001"
MEDIA = "17900000000000001"


class FakeClient:
    def __init__(self, responses=None, pages=None, errors=None):
        self.responses = responses or {}
        self.pages = pages or {}
        self.errors = errors or {}
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        key = (path, (params or {}).get("metric"))
        if key in self.errors or path in self.errors:
            raise self.errors.get(key) or self.errors[path]
        return self.responses.get(key) or self.responses[path]

    async def get_all(self, path, params=None, max_items=None):
        self.calls.append((path, dict(params or {})))
        return [item for page in self.pages[path] for item in page]

    async def iter_pages(self, path, params=None, page_size=None):
        self.calls.append((path, dict(params or {})))
        for page in self.pages[path]:
            yield page


def call(tool, **arguments):
    return json.loads(asyncio.run(tool(**arguments)))


def media(media_id, product, timestamp="2026-10-01T10:00:00+0000"):
    return {"id": media_id, "media_product_type": product, "media_type": "VIDEO", "timestamp": timestamp}


# --- settings -----------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [(IG, IG), (f" {IG} ", IG), ("", None), (None, None)])
def test_parse_ig_user_id(raw, expected):
    assert settings.parse_ig_user_id(raw) == expected


@pytest.mark.parametrize("raw", ["@someone", "1234", "17841400000000001x"])
def test_parse_ig_user_id_rejects_invalid(raw):
    with pytest.raises(ValueError, match="META_DEFAULT_IG_USER_ID"):
        settings.parse_ig_user_id(raw)


@pytest.mark.parametrize("raw, expected", [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), (None, False)])
def test_flag(raw, expected):
    assert settings.is_enabled(raw) is expected


# --- accounts -----------------------------------------------------------------

def test_accounts_lists_only_pages_with_linked_instagram():
    pages = [[{"id": "1", "name": "Page A", "instagram_business_account": {"id": IG, "username": "brand"}},
              {"id": "2", "name": "Page B"}]]
    client = FakeClient(pages={"me/accounts": pages})

    result = call(instagram.make_accounts_tool(lambda: client))

    assert result["accounts"] == [{"page_id": "1", "page_name": "Page A", "ig_user_id": IG, "ig_username": "brand"}]
    assert "instagram_business_account" in client.calls[0][1]["fields"]


# --- reels --------------------------------------------------------------------

def test_reels_filters_reels_and_uses_default_account():
    pages = {f"{IG}/media": [[media("1", "REELS"), media("2", "FEED")], [media("3", "REELS")]]}
    client = FakeClient(pages=pages)

    result = call(instagram.make_reels_tool(lambda: client, IG))

    assert [reel["id"] for reel in result["reels"]] == ["1", "3"]
    assert result["ig_user_id"] == IG
    assert "media_product_type" in client.calls[0][1]["fields"]


def test_reels_stops_at_limit():
    pages = {f"{IG}/media": [[media(str(i), "REELS") for i in range(10)]]}

    result = call(instagram.make_reels_tool(lambda: FakeClient(pages=pages), IG), limit=3)

    assert len(result["reels"]) == 3


def test_reels_stops_at_since_date():
    pages = {f"{IG}/media": [[media("1", "REELS", "2026-10-05T00:00:00+0000"),
                              media("2", "REELS", "2026-09-01T00:00:00+0000"),
                              media("3", "REELS", "2026-08-01T00:00:00+0000")]]}

    result = call(instagram.make_reels_tool(lambda: FakeClient(pages=pages), IG), since="2026-09-15")

    assert [reel["id"] for reel in result["reels"]] == ["1"]


def test_reels_rejects_invalid_since():
    assert "since" in call(instagram.make_reels_tool(lambda: FakeClient(), IG), since="last week")["error"]


def test_reels_without_account_returns_error():
    assert "ig_user_id" in call(instagram.make_reels_tool(lambda: FakeClient(), None))["error"]


def test_reels_rejects_invalid_ig_user_id():
    assert "ig_user_id" in call(instagram.make_reels_tool(lambda: FakeClient(), IG), ig_user_id="me/accounts")["error"]


# --- insights -----------------------------------------------------------------

def insight(name, value, total=False):
    return {"name": name, "period": "lifetime", **({"total_value": {"value": value}} if total else {"values": [{"value": value}]})}


def test_insights_parses_values_and_adds_organic_note():
    metric = ",".join(instagram.REEL_METRICS)
    response = {"data": [insight("reach", 120), insight("views", 300, total=True), insight("likes", 7)]}
    client = FakeClient(responses={(f"{MEDIA}/insights", metric): response})

    result = call(instagram.make_insights_tool(lambda: client), media_id=MEDIA)

    assert result["metrics"]["reach"] == 120
    assert result["metrics"]["views"] == 300
    assert result["metrics"]["shares"] is None
    assert "organic" in result["note"].lower()


def test_insights_falls_back_to_single_metrics():
    metric = ",".join(instagram.REEL_METRICS)
    responses = {(f"{MEDIA}/insights", name): {"data": [insight(name, 1)]} for name in instagram.REEL_METRICS}
    errors = {(f"{MEDIA}/insights", metric): GraphError("unsupported metric", 100),
              (f"{MEDIA}/insights", "reposts"): GraphError("reposts not supported", 100)}
    client = FakeClient(responses=responses, errors=errors)

    result = call(instagram.make_insights_tool(lambda: client), media_id=MEDIA)

    assert result["metrics"]["reach"] == 1
    assert result["metrics"]["reposts"] is None
    assert result["metric_errors"] == {"reposts": "reposts not supported"}


def test_insights_reports_error_when_all_metrics_fail():
    client = FakeClient(errors={f"{MEDIA}/insights": GraphError("Media is not a reel", 100)})

    assert call(instagram.make_insights_tool(lambda: client), media_id=MEDIA)["error"] == "Media is not a reel"


def test_insights_rejects_invalid_media_id():
    assert "media_id" in call(instagram.make_insights_tool(lambda: FakeClient()), media_id=MEDIA + "/../me")["error"]


# --- business discovery -------------------------------------------------------

def discovery_response(*items):
    return {"business_discovery": {"username": "other", "followers_count": 10, "media": {"data": list(items)}}, "id": IG}


def test_public_media_builds_field_expansion_and_filters_reels():
    client = FakeClient(responses={IG: discovery_response(media("1", "REELS"), media("2", "FEED"))})

    result = call(instagram.make_public_media_tool(lambda: client, IG), username="other.brand", limit=5)

    fields = client.calls[0][1]["fields"]
    assert "business_discovery.username(other.brand)" in fields
    assert "media.limit(5)" in fields
    assert [item["id"] for item in result["media"]] == ["1"]
    assert result["account"]["followers_count"] == 10


@pytest.mark.parametrize("username", ["a){media}", "with space", "x" * 31, ""])
def test_public_media_rejects_unsafe_usernames(username):
    assert "username" in call(instagram.make_public_media_tool(lambda: FakeClient(), IG), username=username)["error"]


# --- registration -------------------------------------------------------------

def test_register_adds_read_only_instagram_tools():
    server = FastMCP("test")

    instagram.register(server, lambda: FakeClient(), IG)
    tool_policy.apply(server)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(instagram.TOOL_NAMES) <= set(tools)
    assert all(tools[name].annotations.readOnlyHint for name in instagram.TOOL_NAMES)


def test_extensions_register_instagram_only_when_enabled():
    import extensions

    disabled, enabled = FastMCP("a"), FastMCP("b")
    extensions.register(disabled, {}, client_factory=lambda: FakeClient())
    extensions.register(enabled, {"META_ENABLE_INSTAGRAM_INSIGHTS": "1"}, client_factory=lambda: FakeClient())

    assert not {t.name for t in disabled._tool_manager.list_tools()} & set(instagram.TOOL_NAMES)
    assert set(instagram.TOOL_NAMES) <= {t.name for t in enabled._tool_manager.list_tools()}


def test_reels_report_why_they_stopped():
    pages = {f"{IG}/media": [[media(str(i), "REELS") for i in range(5)]]}

    assert call(instagram.make_reels_tool(lambda: FakeClient(pages=pages), IG), limit=2)["stopped_by"] == "limit"
    assert call(instagram.make_reels_tool(lambda: FakeClient(pages=pages), IG), limit=50)["stopped_by"] == "end"


@pytest.mark.parametrize("raw", ["１２３４５６", "١٢٣٤٥٦"])
def test_non_ascii_digits_are_rejected(raw):
    with pytest.raises(ValueError):
        settings.parse_ig_user_id(raw)
    with pytest.raises(ValueError):
        settings.parse_account_id(raw)
