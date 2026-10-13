import asyncio
import json
from datetime import datetime, timezone

import pytest
from mcp.server.fastmcp import FastMCP

import extensions
import tool_policy

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, campaigns=None, error=None):
        self.campaigns = campaigns or []
        self.error = error
        self.calls = []

    async def get_all(self, path, params=None, max_items=None):
        self.calls.append((path, params, max_items))
        if self.error:
            raise self.error
        return self.campaigns


def call(tool, **arguments):
    return json.loads(asyncio.run(tool(**arguments)))


@pytest.mark.parametrize("raw, expected", [("act_123456", "act_123456"), ("123456", "act_123456"), (" act_42 ", "act_42"), ("", None), (None, None)])
def test_parse_account_id(raw, expected):
    assert extensions.parse_account_id(raw) == expected


@pytest.mark.parametrize("raw", ["act_", "act_12a", "account 1", "act_1;drop"])
def test_parse_account_id_rejects_invalid(raw):
    with pytest.raises(ValueError, match="META_DEFAULT_ACCOUNT_ID"):
        extensions.parse_account_id(raw)


def test_delivery_tool_uses_default_account_and_requests_needed_fields():
    client = FakeClient([{"id": "1", "name": "A", "status": "ACTIVE", "effective_status": "ACTIVE"}])
    tool = extensions.make_campaign_delivery_tool(lambda: client, "act_9", lambda: NOW)

    result = call(tool)

    [(path, params, max_items)] = client.calls
    assert path == "act_9/campaigns"
    assert "effective_status" in params["fields"] and "stop_time" in params["fields"] and "adsets" in params["fields"]
    assert max_items == extensions.DEFAULT_CAMPAIGN_LIMIT
    assert result["account_id"] == "act_9"
    assert result["counts"] == {"ACTIVE": 1}


def test_delivery_tool_explicit_account_wins_and_is_normalized():
    client = FakeClient()
    tool = extensions.make_campaign_delivery_tool(lambda: client, "act_9", lambda: NOW)

    call(tool, account_id="777", limit=5)

    assert client.calls[0][0] == "act_777/campaigns"
    assert client.calls[0][2] == 5


def test_delivery_tool_without_any_account_returns_error():
    tool = extensions.make_campaign_delivery_tool(lambda: FakeClient(), None, lambda: NOW)

    assert "account_id" in call(tool)["error"]


def test_delivery_tool_reports_graph_errors():
    from graph_client import GraphError

    tool = extensions.make_campaign_delivery_tool(lambda: FakeClient(error=GraphError("Unsupported get request", 100)), "act_9", lambda: NOW)

    assert call(tool)["error"] == "Unsupported get request"


def test_delivery_tool_caps_limit():
    client = FakeClient()
    tool = extensions.make_campaign_delivery_tool(lambda: client, "act_9", lambda: NOW)

    call(tool, limit=10_000)

    assert client.calls[0][2] == extensions.MAX_CAMPAIGN_LIMIT


def test_register_adds_read_only_tool_and_default_account_instructions():
    server = FastMCP("test")

    extensions.register(server, {"META_DEFAULT_ACCOUNT_ID": "act_9"}, client_factory=lambda: FakeClient())
    tool_policy.apply(server)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert tools["get_campaign_delivery"].annotations.readOnlyHint is True
    assert "act_9" in server._mcp_server.instructions


def test_register_keeps_existing_instructions():
    server = FastMCP("test", instructions="Upstream hint.")

    extensions.register(server, {"META_DEFAULT_ACCOUNT_ID": "act_9"}, client_factory=lambda: FakeClient())

    assert server._mcp_server.instructions.startswith("Upstream hint.")
    assert "act_9" in server._mcp_server.instructions


def test_register_without_default_account_leaves_instructions():
    server = FastMCP("test")

    extensions.register(server, {}, client_factory=lambda: FakeClient())

    assert server._mcp_server.instructions is None


def test_launcher_tools_are_classified_and_do_not_clash_with_upstream():
    from tests import upstream_scan

    assert set(extensions.TOOL_NAMES) == set(tool_policy.LAUNCHER_TOOL_CATEGORIES)
    assert not set(extensions.TOOL_NAMES) & upstream_scan.registered_tool_names()
