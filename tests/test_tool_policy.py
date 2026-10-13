import asyncio
import logging

from mcp.server.fastmcp import FastMCP

import tool_policy


def make_server(*names):
    server = FastMCP("test")
    for name in names:
        async def tool() -> str:
            return "ok"
        server.add_tool(tool, name=name)
    return server


def listed_tools(server):
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_read_tools_are_read_only_and_idempotent():
    server = make_server("get_campaigns")

    unknown = tool_policy.apply(server)
    annotations = listed_tools(server)["get_campaigns"].annotations

    assert unknown == []
    assert annotations.readOnlyHint is True
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is True


def test_create_tools_are_additive_not_destructive():
    server = make_server("create_campaign", "upload_ad_image")
    tool_policy.apply(server)

    for tool in listed_tools(server).values():
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False


def test_update_tools_and_budget_schedules_are_destructive():
    server = make_server("update_campaign", "create_budget_schedule")
    tool_policy.apply(server)

    for tool in listed_tools(server).values():
        assert tool.annotations.destructiveHint is True


def test_local_file_writer_is_not_open_world():
    server = make_server("save_ad_image_locally")
    tool_policy.apply(server)

    annotations = listed_tools(server)["save_ad_image_locally"].annotations
    assert annotations.readOnlyHint is False
    assert annotations.openWorldHint is False


def test_unknown_tools_are_treated_as_destructive_and_reported(caplog):
    server = make_server("brand_new_tool", "get_ads")

    with caplog.at_level(logging.WARNING):
        unknown = tool_policy.apply(server)

    assert unknown == ["brand_new_tool"]
    assert listed_tools(server)["brand_new_tool"].annotations.destructiveHint is True
    assert "brand_new_tool" in caplog.text


def test_large_result_tools_declare_max_result_size_in_meta():
    server = make_server("get_insights", "get_ads", "get_account_info")
    tool_policy.apply(server)
    tools = listed_tools(server)

    assert tools["get_insights"].meta == {"anthropic/maxResultSizeChars": 500_000}
    assert tools["get_ads"].meta == {"anthropic/maxResultSizeChars": 250_000}
    assert tools["get_account_info"].meta is None


def test_result_size_limits_respect_claude_code_ceiling():
    assert max(tool_policy.RESULT_SIZE_LIMITS.values()) <= tool_policy.MAX_RESULT_SIZE_CEILING
    assert set(tool_policy.RESULT_SIZE_LIMITS) <= set(tool_policy.TOOL_CATEGORIES)


def test_every_category_is_known():
    assert set(tool_policy.TOOL_CATEGORIES.values()) <= set(tool_policy.CATEGORY_ANNOTATIONS)
